"""Archive Parrot drone manuals, white papers and current developer documentation."""

import argparse
import csv
import hashlib
import html as html_lib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from download_dji_documents import (
    EXTENSIONS, download, file_hash, session, validate_document, write_csv,
)

BASE = 'https://www.parrot.com'
SUPPORT_MODELS = {
    'anafi', 'anafi-ai', 'anafi-usa', 'anafi-thermal', 'bebop-range', 'disco-range',
    'disco-pro-ag', 'sequoia', 'ar-drone', 'swing', 'hydrofoil', 'airborne',
    'rolling-spider', 'mambo-range',
}
SDK_ROOTS = {
    'airsdk', 'groundsdk-ios', 'groundsdk-android', 'groundsdk-tools', 'olympe',
    'sphinx', 'mavlink-flightplan',
    'groundsdk-ios-samples', 'groundsdk-android-samples', 'arsdk-protocol',
}
SEEDS = [
    BASE + '/en/support/documentation',
    BASE + '/en/support/documentation/anafi-ai',
    BASE + '/en/support/documentation/anafi-usa',
    BASE + '/en/support-documentation-anafi-ukr',
    BASE + '/en/drones/anafi-ai/technical-documentation/technical-specifications',
    BASE + '/en/drones/anafi-usa/rugged-easily-deployable',
    'https://developer.parrot.com/docs/index.html',
    'https://developer.parrot.com/docs/airsdk/anafi-ai/general/overview.html',
    'https://developer.parrot.com/docs/airsdk/anafi-ukr/general/overview.html',
]
PAGE_FIELDS = ['url', 'title', 'status', 'local_path', 'text_path', 'bytes', 'sha256', 'error']
FILE_FIELDS = ['slug', 'title', 'page_url', 'url', 'local_path', 'status', 'bytes', 'sha256', 'error']


def trusted_url(url):
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    return p.scheme in {'https', 'http'} and (host == 'parrot.com' or host.endswith('.parrot.com'))


def clean_url(url, base=BASE):
    url = urldefrag(urljoin(base, url.strip()))[0]
    # Parrot's support directory still links to the pre-split ANAFI Ai SDK path.
    url = url.replace('https://developer.parrot.com/docs/airsdk/general/',
                      'https://developer.parrot.com/docs/airsdk/anafi-ai/general/')
    url = url.replace('https://developer.parrot.com/docs/sphinx/system-requirements.html',
                      'https://developer.parrot.com/docs/sphinx/system_requirements.html')
    url = url.replace('https://developer.parrot.com/docs/sphinx/unreal_segcam.html',
                      'https://developer.parrot.com/docs/sphinx/segmentation_cam.html')
    p = urlsplit(url)
    # Literal spaces occur in Parrot's older PDF links.
    return urlunsplit((p.scheme, p.netloc, p.path.replace(' ', '%20'), p.query, ''))


def is_page(url):
    if not trusted_url(url):
        return False
    p = urlsplit(url)
    path = unquote(p.path).rstrip('/')
    if p.query:
        return False
    if p.hostname == 'developer.parrot.com':
        if path in {'/docs', '/docs/index.html'}:
            return True
        # shortcut: keep API overviews; add symbol directories for a full API mirror.
        if path in {'/docs/refdoc-ios/index.html', '/docs/refdoc-android/index.html'}:
            return True
        parts = path.split('/')
        if len(parts) < 4 or parts[1] != 'docs' or parts[2] not in SDK_ROOTS:
            return False
        if parts[2] == 'airsdk' and parts[3] not in {'anafi-ai', 'anafi-ukr'}:
            return False
        if any(re.fullmatch(r'v?\d+\.\d+(?:\.[\w.-]+)?', part) for part in parts[3:]):
            return False
        if any(part in {'_sources', '_static', '_images', 'archive', 'old'} for part in parts):
            return False
        return path.endswith('.html') and parts[-1] not in {'search.html', 'genindex.html', 'py-modindex.html'}
    if p.hostname not in {'www.parrot.com', 'parrot.com'}:
        return False
    if path == '/en/support/documentation':
        return True
    prefix = '/en/support/documentation/'
    if path.startswith(prefix):
        return path[len(prefix):] in SUPPORT_MODELS
    if path in {'/en/support-documentation-anafi-ukr', '/en/chuck-30-technical-specifications'}:
        return True
    if re.fullmatch(r'/en/support/(?:anafi(?:-ai|-usa|-thermal)?|bebop-range|disco-range)', path):
        return True
    return bool(re.fullmatch(
        r'/en/drones/(?:anafi(?:-ai|-usa|-thermal)?)/(?:technical-documentation(?:/[^/]+)?|'
        r'technical-specifications\s*|rugged-easily-deployable)', path))


def parse_sitemap(xml):
    return list(dict.fromkeys(clean_url(node.text) for node in ET.fromstring(xml).iter()
                              if node.tag.endswith('}loc') and node.text and is_page(clean_url(node.text))))


def fetch(url, **kwargs):
    for _ in range(6):
        if not trusted_url(url):
            raise ValueError('拒绝非 Parrot 官方域名: ' + url)
        client = session()
        client.headers.update({'User-Agent': 'ParrotDocumentArchive/1.0', 'Accept-Language': 'en'})
        response = client.get(url, timeout=(15, 60), allow_redirects=False, **kwargs)
        if response.is_redirect:
            url = clean_url(response.headers['Location'], url)
            response.close()
            continue
        response.raise_for_status()
        return response
    raise ValueError('重定向次数过多: ' + url)


def group_for(url):
    path = unquote(urlsplit(url).path).strip('/').split('/')
    if urlsplit(url).hostname == 'developer.parrot.com':
        return 'sdk-' + (path[1] if len(path) > 2 else 'index')
    if 'drones' in path:
        return path[path.index('drones') + 1]
    return path[-1].replace('support-documentation-', '')


def parse_links(html, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    documents, pages = {}, {}
    for anchor in soup.select('a[href]'):
        url = clean_url(anchor['href'], page_url)
        if is_page(url):
            pages.setdefault(url, None)
        suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
        if suffix not in EXTENSIONS or not trusted_url(url):
            continue
        title = re.sub(r'\[图像: [^\]]*\]', '', anchor.get_text(' ', strip=True)).strip()
        if title.lower() in {'download', 'download pdf', 'pdf', 'télécharger', ''}:
            block = anchor.find_parent(class_=lambda c: c and 'Basic_basic_block' in c)
            heading = block.find(['h2', 'h3']) if block else anchor.find_previous(['h2', 'h3'])
            title = heading.get_text(' ', strip=True) if heading else unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        documents.setdefault(url, {'slug': group_for(page_url), 'title': title,
                                   'page_url': page_url, 'url': url})
    return list(documents.values()), list(pages)


def snapshot(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('article[role="main"], article, div[role="main"], main, #content, .contentContainer, .content')
    if content is None:
        raise ValueError('没有找到技术文档正文，可能是错误页面或动态内容')
    for node in content.select('script, style, nav, header, footer, iframe, form, svg, button, link'):
        node.decompose()
    heading = content.find('h1')
    title = heading.get_text(' ', strip=True) if heading else (
        soup.title.get_text(' ', strip=True) if soup.title else group_for(url))
    if not content.get_text(' ', strip=True):
        raise ValueError('文档正文为空')
    for anchor in content.select('a[href]'):
        href = clean_url(anchor['href'], url)
        if urlsplit(href).scheme in {'http', 'https'}:
            anchor['href'] = href
        else:
            del anchor['href']
    for image in content.select('img'):
        image.replace_with('[图像: ' + (image.get('alt') or '见原始网页') + ']')
    for node in content.find_all(True):
        for attribute in list(node.attrs):
            if attribute not in {'href', 'colspan', 'rowspan', 'id'}:
                del node[attribute]
    source = html_lib.escape(url, quote=True)
    rendered = '<!doctype html><html><head><meta charset="utf-8"><title>' + html_lib.escape(title)
    rendered += '</title><style>body{max-width:1050px;margin:32px auto;padding:0 24px;font:16px/1.6 sans-serif}pre{white-space:pre-wrap}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px}</style></head><body>'
    rendered += f'<p>Source: <a href="{source}">{source}</a></p>{content}</body></html>'
    text = title + '\nSource: ' + url + '\n\n' + content.get_text('\n', strip=True) + '\n'
    return title, rendered, text


def archive_page(url, output, cached=None):
    result = {'url': url, 'title': '', 'status': 'failed', 'local_path': '', 'text_path': '',
              'bytes': 0, 'sha256': '', 'error': ''}
    try:
        if cached and cached['status'] in {'saved', 'existing'}:
            path = (output / cached['local_path']).resolve()
            text_path = (output / cached['text_path']).resolve()
            if (path.is_relative_to(output) and text_path.is_relative_to(output)
                    and path.is_file() and text_path.is_file() and file_hash(path) == cached['sha256']):
                docs, pages = parse_links(path.read_text(encoding='utf-8'), url)
                return {**cached, 'status': 'existing', 'bytes': path.stat().st_size}, docs, pages
        time.sleep(0.2)
        with fetch(url) as response:
            response.encoding = 'utf-8'
            html = response.text
            final_url = clean_url(response.url)
            if not is_page(final_url):
                raise ValueError('页面跳转到采集范围之外: ' + final_url)
        docs, pages = parse_links(html, final_url)
        title, rendered, text = snapshot(html, final_url)
        identity = hashlib.sha256(url.encode()).hexdigest()[:16]
        folder = output / '网页文档' / group_for(final_url)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (identity + '.html')
        text_path = folder / (identity + '.txt')
        for target, value in [(path, rendered), (text_path, text)]:
            temporary = target.with_suffix(target.suffix + '.part')
            temporary.write_text(value, encoding='utf-8')
            temporary.replace(target)
        result.update(title=title, status='saved', local_path=str(path.relative_to(output)),
                      text_path=str(text_path.relative_to(output)), bytes=path.stat().st_size,
                      sha256=file_hash(path))
        return result, docs, pages
    except (requests.RequestException, OSError, ValueError) as exc:
        result['error'] = str(exc)
        return result, [], []


def download_file(document, output, cached=None):
    if cached and cached.get('status') in {'downloaded', 'existing'} and cached.get('local_path'):
        path = (output / cached['local_path']).resolve()
        try:
            if path.is_relative_to(output) and path.is_file() and file_hash(path) == cached['sha256']:
                validate_document(path, path.suffix.lower())
                return {'status': 'existing', 'local_path': cached['local_path'], 'bytes': path.stat().st_size,
                        'sha256': cached['sha256'], 'error': ''}
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
    return download(document, output, fetcher=fetch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('Parrot无人机文档'))
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--list-only', action='store_true', help='采集网页与文件清单，暂不下载 PDF 等附件')
    parser.add_argument('--resume', action='store_true', help='复用已校验的网页快照，补抓失败页面和新发现链接')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    seeds = list(SEEDS)
    discovery_error = ''
    try:
        with fetch(BASE + '/sitemap.xml') as response:
            seeds.extend(parse_sitemap(response.text))
    except (requests.RequestException, OSError, ValueError, ET.ParseError) as exc:
        discovery_error = str(exc)
        print('站点地图发现失败，将继续已知入口: ' + discovery_error, flush=True)
    cached_pages = {}
    page_log = output / '网页采集记录.csv'
    if args.resume and page_log.exists():
        with page_log.open(encoding='utf-8-sig', newline='') as previous:
            cached_pages = {row['url']: row for row in csv.DictReader(previous)}
        seeds.extend(clean_url(url) for url, row in cached_pages.items()
                     if is_page(clean_url(url)) or row['status'] in {'saved', 'existing'})
    queue, seen = deque(dict.fromkeys(seeds)), set(seeds)
    pages, documents, associations = [], {}, []
    with page_log.open('w', encoding='utf-8-sig', newline='') as log:
        writer = csv.DictWriter(log, fieldnames=PAGE_FIELDS)
        writer.writeheader()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = {}
            while queue or tasks:
                while queue and len(tasks) < args.workers:
                    url = queue.popleft()
                    tasks[pool.submit(archive_page, url, output, cached_pages.get(url))] = url
                finished, _ = wait(tasks, return_when=FIRST_COMPLETED)
                for task in finished:
                    tasks.pop(task)
                    result, found_docs, found_pages = task.result()
                    pages.append(result)
                    writer.writerow(result)
                    log.flush()
                    for doc in found_docs:
                        documents.setdefault(doc['url'], doc)
                        associations.append(doc)
                    for url in found_pages:
                        if url not in seen:
                            seen.add(url)
                            queue.append(url)
                    print(f'网页 {len(pages)}/{len(seen)} {result["status"]}: {result["url"]} {result["error"]}', flush=True)
    (output / '文档清单.json').write_text(json.dumps(associations, ensure_ascii=False, indent=2), encoding='utf-8')
    results = {}
    cached_files = {}
    file_index = output / '文档索引.csv'
    if file_index.exists():
        with file_index.open(encoding='utf-8-sig', newline='') as previous:
            cached_files = {row['url']: row for row in csv.DictReader(previous)}
    print(f'发现 {len(documents)} 个附件链接，开始下载。', flush=True)
    if not args.list_only:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = {pool.submit(download_file, doc, output, cached_files.get(url)): url
                     for url, doc in documents.items()}
            for i, task in enumerate(as_completed(tasks), 1):
                url = tasks[task]
                results[url] = result = task.result()
                print(f'附件 {i}/{len(tasks)} {result["status"]}: {documents[url]["title"]} {result["error"]}', flush=True)
                with (output / '下载进度.jsonl').open('a', encoding='utf-8') as journal:
                    journal.write(json.dumps({'url': url, **result}, ensure_ascii=False) + '\n')
    rows = [{**doc, **results.get(doc['url'], {'status': 'listed'})} for doc in associations]
    write_csv(output / '文档索引.csv', rows, FILE_FIELDS)
    summary = {'pages': len(pages), 'page_results': dict(Counter(p['status'] for p in pages)),
               'unique_files': len(documents), 'document_associations': len(associations),
               'file_results': dict(Counter(r['status'] for r in results.values())),
               'document_bytes': sum(r['bytes'] for r in results.values()),
               'sitemap_error': discovery_error, 'output': str(output)}
    (output / '汇总.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return int(bool(discovery_error or summary['page_results'].get('failed') or summary['file_results'].get('failed')))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (requests.RequestException, OSError, ValueError) as exc:
        print('错误: ' + str(exc), file=sys.stderr)
        sys.exit(1)
