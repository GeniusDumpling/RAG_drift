"""Download public documents from DJI drone product pages (no firmware/software)."""

import argparse
import csv
import hashlib
import json
import re
import sys
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INDEX_URL = 'https://www.dji.com/cn/downloads'
EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.rtf', '.csv'}
FIELDS = ['slug', 'name', 'title', 'date', 'page_url', 'url', 'local_path',
          'status', 'bytes', 'sha256', 'error']
LOCAL = threading.local()


def trusted_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme in {'http', 'https'} and any(
        host == domain or host.endswith('.' + domain)
        for domain in ('dji.com', 'djicdn.com'))


def session():
    if not hasattr(LOCAL, 'session'):
        client = requests.Session()
        client.headers.update({'User-Agent': 'DJIDocumentArchive/1.0',
                               'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.5'})
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        client.mount('https://', HTTPAdapter(max_retries=retry))
        client.mount('http://', HTTPAdapter(max_retries=retry))
        LOCAL.session = client
    return LOCAL.session


def fetch(url, **kwargs):
    # Check each redirect before following it, so links stay on DJI's domains.
    for _ in range(6):
        if not trusted_url(url):
            raise ValueError('拒绝非大疆域名: ' + url)
        response = session().get(url, timeout=(15, 90), allow_redirects=False, **kwargs)
        if response.is_redirect:
            url = urljoin(url, response.headers['Location'])
            response.close()
            continue
        response.raise_for_status()
        return response
    raise ValueError('重定向次数过多: ' + url)


def is_drone(slug):
    # shortcut: whitelist drone families; update when DJI introduces a new family.
    return bool(re.fullmatch(
        r'(?:mavic(?:-.*)?|air-.*|mini-.*|lito-.*|flip(?:-.*)?|neo(?:-.*)?|'
        r'avata(?:-.*)?|inspire-.*|phantom-.*|matrice(?:-.*|\d.*)|'
        r't\d+[a-z]*(?:-.*)?|mg-1.*|flycart-.*|robomaster-tt(?:-.*)?|'
        r'spark|dji-fpv|p4-multispectral)', slug))


def parse_products(html, page_url):
    products = {}
    for anchor in BeautifulSoup(html, 'html.parser').select('a[href]'):
        url = urldefrag(urljoin(page_url, anchor['href']))[0]
        parsed = urlsplit(url)
        match = re.fullmatch(r'/(?:cn/)?downloads/products/([a-z0-9-]+)/?', parsed.path)
        if parsed.hostname != 'www.dji.com' or not match or not is_drone(match[1]):
            continue
        slug = match[1]
        products.setdefault(slug, {'slug': slug, 'name': anchor.get_text(' ', strip=True),
                                   'page_url': 'https://www.dji.com/cn/downloads/products/' + slug})
    return list(products.values())


def parse_documents(html, product):
    documents = {}
    soup = BeautifulSoup(html, 'html.parser')
    for anchor in soup.select('a[href]'):
        url = urldefrag(urljoin(product['page_url'], anchor['href']))[0]
        suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
        if suffix not in EXTENSIONS or not trusted_url(url):
            continue
        item = anchor.find_parent(class_=lambda c: c and '__document-item___' in c)
        title = item.select_one('[class*="__document-name___"]') if item else None
        date = item.select_one('[class*="__document-time___"]') if item else None
        documents.setdefault(url, {
            **product, 'title': title.get_text(' ', strip=True) if title else
            unquote(urlsplit(url).path.rsplit('/', 1)[-1]),
            'date': date.get_text(' ', strip=True) if date else '', 'url': url})
    return list(documents.values())


def validate_document(path, suffix):
    with path.open('rb') as stream:
        head = stream.read(1024)
    if not head or head.lstrip().lower().startswith((b'<html', b'<!doctype', b'<?xml')):
        raise ValueError('返回空文件或网页，非文档')
    if suffix == '.pdf' and b'%PDF-' not in head:
        raise ValueError('PDF 文件头无效')
    if suffix in {'.doc', '.xls', '.ppt'} and not head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('Office 文件头无效')
    if suffix in {'.docx', '.xlsx', '.pptx'}:
        with zipfile.ZipFile(path) as archive:
            if '[Content_Types].xml' not in archive.namelist():
                raise ValueError('Office 文档格式无效')
    if suffix == '.rtf' and not head.startswith(b'{\\rtf'):
        raise ValueError('RTF 文件头无效')


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(document, output, fetcher=None):
    suffix = Path(unquote(urlsplit(document['url']).path)).suffix.lower()
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', document['title']).strip(' .')[:70] or 'document'
    # URL suffix prevents equal document titles from overwriting different files.
    identity = hashlib.sha256(document['url'].encode()).hexdigest()[:12]
    path = output / document['slug'] / (title + '_' + identity + suffix)
    temporary = path.with_suffix(path.suffix + '.part')
    result = {'local_path': str(path.relative_to(output)), 'error': ''}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            validate_document(path, suffix)
            return {**result, 'status': 'existing', 'bytes': path.stat().st_size,
                    'sha256': file_hash(path)}
        time.sleep(0.25)
        with (fetcher or fetch)(document['url'], stream=True, headers={'Referer': document['page_url']}) as response:
            with temporary.open('wb') as stream:
                for block in response.iter_content(128 * 1024):
                    if block:
                        stream.write(block)
            expected = response.headers.get('Content-Length')
            if expected and not response.headers.get('Content-Encoding'):
                if temporary.stat().st_size != int(expected):
                    raise ValueError('文件长度与服务器声明不一致')
        validate_document(temporary, suffix)
        digest = file_hash(temporary)
        temporary.replace(path)
        return {**result, 'status': 'downloaded', 'bytes': path.stat().st_size, 'sha256': digest}
    except (requests.RequestException, OSError, ValueError, zipfile.BadZipFile) as exc:
        error = str(exc)
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            error += '; 临时文件清理失败: ' + str(cleanup_error)
        return {**result, 'status': 'failed', 'bytes': 0, 'sha256': '', 'error': error}


def write_csv(path, rows, fields):
    temporary = path.with_suffix(path.suffix + '.part')
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('DJI无人机文档'), help='保存目录')
    parser.add_argument('--models', nargs='+', help='仅获取指定机型 slug，例如 mini-4-pro matrice-350-rtk')
    parser.add_argument('--list-only', action='store_true', help='只生成文档清单，不下载文件')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=3, help='下载并发数，1–4')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with fetch(INDEX_URL) as response:
        response.encoding = 'utf-8'
        products = parse_products(response.text, response.url)
    if not products:
        raise ValueError('未发现无人机机型，请检查官网页面结构或网络返回内容')
    if args.models:
        missing = set(args.models) - {p['slug'] for p in products}
        if missing:
            raise ValueError('未发现或不属于无人机的机型: ' + ', '.join(sorted(missing)))
        products = [p for p in products if p['slug'] in args.models]
    print(f'发现 {len(products)} 个无人机产品页面。', flush=True)
    pages, documents = [], []
    for i, product in enumerate(products, 1):
        page = {**product, 'count': 0, 'status': 'ok', 'error': ''}
        try:
            with fetch(product['page_url']) as response:
                response.encoding = 'utf-8'
                if not urlsplit(response.url).path.startswith('/cn/'):
                    raise ValueError('中文页面跳转到了其他地区，已记录以避免静默遗漏中文文档')
                docs = parse_documents(response.text, product)
            if not docs:
                raise ValueError('未发现文档，请检查该页面是否改版或文档是否动态加载')
            documents.extend(docs)
            page['count'] = len(docs)
        except (requests.RequestException, OSError, ValueError) as exc:
            page.update(status='failed', error=str(exc))
        pages.append(page)
        write_csv(output / '机型采集记录.csv', pages,
                  ['slug', 'name', 'page_url', 'count', 'status', 'error'])
        print(f'[{i}/{len(products)}] {product["name"]}: {page["count"]} 份 {page["error"]}', flush=True)
        time.sleep(0.3)
    (output / '文档清单.json').write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding='utf-8')
    if not documents:
        raise ValueError('没有可下载文档，详见机型采集记录.csv')
    unique = {doc['url']: doc for doc in reversed(documents)}
    print(f'共 {len(documents)} 条文档关联，{len(unique)} 个不重复文件链接。', flush=True)
    results = {}
    if not args.list_only:
        # One transfer per URL; the CSV preserves all product-to-document associations.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = {pool.submit(download, doc, output): url for url, doc in unique.items()}
            for i, task in enumerate(as_completed(tasks), 1):
                url = tasks[task]
                result = task.result()
                results[url] = result
                print(f'[{i}/{len(tasks)}] {result["status"]}: {unique[url]["title"]} {result["error"]}', flush=True)
                with (output / '下载进度.jsonl').open('a', encoding='utf-8') as journal:
                    journal.write(json.dumps({'url': url, **result}, ensure_ascii=False) + '\n')
    rows = [{**doc, **results.get(doc['url'], {'status': 'listed'})} for doc in documents]
    write_csv(output / '文档索引.csv', rows, FIELDS)
    summary = {'products': len(products), 'page_failures': sum(p['status'] == 'failed' for p in pages),
               'document_associations': len(documents), 'unique_urls': len(unique),
               'results': dict(Counter(r['status'] for r in results.values())),
               'total_bytes': sum(r['bytes'] for r in results.values()), 'output': str(output)}
    (output / '汇总.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 1 if summary['page_failures'] or summary['results'].get('failed') else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (requests.RequestException, OSError, ValueError) as error:
        print(f'错误: {error}', file=sys.stderr)
        sys.exit(1)
