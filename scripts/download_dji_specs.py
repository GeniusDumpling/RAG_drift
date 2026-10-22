"""Supplement existing DJI drone PDFs with Chinese technical specification pages."""

import argparse
import csv
import html as html_lib
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from download_dji_documents import fetch, file_hash, write_csv
from download_parrot_documents import snapshot

BASE = 'https://www.dji.com'
FIELDS = ['slug', 'name', 'status', 'source_url', 'collected_at', 'group_count',
          'parameter_count', 'html_path', 'text_path', 'sha256', 'error']


def load_products(output):
    products = {}
    with (output / '文档索引.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            slug = row['slug']
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
                raise ValueError('索引中的机型目录名无效: ' + slug)
            products.setdefault(slug, {'slug': slug, 'name': row['name'], 'page_url': row['page_url']})
    if not products:
        raise ValueError('文档索引中没有机型')
    return list(products.values())


def normal_name(name):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', name.lower())


def support_candidates(product, catalog):
    exact, named, variants = [], [], []
    for anchor in BeautifulSoup(catalog, 'html.parser').select('a[href]'):
        url = urldefrag(urljoin(BASE, anchor['href']))[0]
        p = urlsplit(url)
        match = re.fullmatch(r'/(?:cn/)?support/product/([a-z0-9-]+)/?', p.path)
        if p.hostname != 'www.dji.com' or not match:
            continue
        url = BASE + '/cn/support/product/' + match[1]
        # The download center combines these variants; support lists two pages.
        if product['slug'] == 'inspire-1-pro-and-raw' and match[1] in {'inspire-1-pro', 'inspire-1-raw'}:
            variants.append(url)
        if match[1] == product['slug']:
            exact.append(url)
        elif normal_name(anchor.get_text(' ', strip=True)) == normal_name(product['name']):
            named.append(url)
    fallback = BASE + '/cn/support/product/' + product['slug']
    if variants:
        return list(dict.fromkeys(variants))
    return list(dict.fromkeys(exact + named + [fallback]))


def render_specs(html, name, source_url, collected_at):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('#elevator_floor_specs, [class*="__specs-container___"], .spec-content')
    if content is None:
        raise ValueError('未找到技术参数区域')
    keys = content.select('[class*="__specs-detail-key___"], .table-spec h5')
    groups = content.select('[class*="__specs-group-title___"], .table-spec th h4')
    if not keys:
        raise ValueError('技术参数区域为空，未找到参数字段')
    group_count, parameter_count = len(groups), len(keys)
    for heading in groups:
        heading.name = 'h2'
    for key in keys:
        key.insert_after(soup.new_tag('br'))
    page_title = soup.find('h1')
    display_name = page_title.get_text(' ', strip=True).removesuffix('技术支持').strip() if page_title else name
    wrapper = '<main><h1>' + html_lib.escape(display_name) + ' 技术参数</h1>'
    wrapper += '<p>采集时间（北京时间）：' + html_lib.escape(collected_at) + '</p>'
    wrapper += str(content) + '</main>'
    _, rendered, text = snapshot(wrapper, source_url)
    return {'html': rendered, 'text': text, 'group_count': group_count, 'parameter_count': parameter_count}


def capture(product, output, catalog):
    row = {**product, 'status': 'failed', 'source_url': '', 'collected_at': '',
           'group_count': 0, 'parameter_count': 0, 'html_path': '', 'text_path': '',
           'sha256': '', 'error': ''}
    errors, parts, sources = [], [], []
    partial_note = ''
    required = 2 if product['slug'] == 'inspire-1-pro-and-raw' else 1
    collected = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    for url in support_candidates(product, catalog):
        try:
            time.sleep(0.25)
            with fetch(url) as response:
                response.encoding = 'utf-8'
                source = response.url
                if '/cn/' not in urlsplit(source).path:
                    raise ValueError('中文页面跳转到其他地区: ' + source)
                result = render_specs(response.text, product['name'], source, collected)
            parts.append(result)
            sources.append(source)
            if len(parts) == required:
                break
        except (requests.RequestException, OSError, ValueError) as exc:
            errors.append(url + ': ' + str(exc))
    if not parts and product['slug'] == 'inspire-1-pro-and-raw':
        url = 'https://www-v1.dji.com/cn/inspire-1-pro-and-raw/spec.html'
        try:
            with fetch(url) as response:
                response.encoding = 'utf-8'
                partial_note = '仅包含 Inspire 1 Pro 官方旧版参数；RAW 版本参数未补齐。'
                result = render_specs(response.text, 'Inspire 1 Pro', response.url, collected)
                rendered = BeautifulSoup(result['html'], 'html.parser')
                note = rendered.new_tag('p')
                note.string = partial_note
                rendered.body.insert(0, note)
                result['html'] = str(rendered)
                result['text'] = partial_note + '\n\n' + result['text']
                parts.append(result)
                sources.append(response.url)
                required = 1
        except (requests.RequestException, OSError, ValueError) as exc:
            errors.append(url + ': ' + str(exc))
    if len(parts) != required:
        row['error'] = '\n'.join(errors) or '未获取合并条目的全部型号参数'
        return row
    try:
        rendered = BeautifulSoup(parts[0]['html'], 'html.parser')
        if len(parts) > 1:
            rendered.title.string = product['name'] + ' 技术参数'
            for part in parts[1:]:
                rendered.body.append(rendered.new_tag('hr'))
                body = BeautifulSoup(part['html'], 'html.parser').body
                for child in list(body.contents):
                    rendered.body.append(child)
        folder = (output / product['slug']).resolve()
        if not folder.is_relative_to(output):
            raise ValueError('机型目录超出保存范围')
        folder.mkdir(parents=True, exist_ok=True)
        html_path, text_path = folder / '技术参数.html', folder / '技术参数.txt'
        for target, value in [(html_path, str(rendered)), (text_path, '\n\n'.join(p['text'] for p in parts))]:
            temporary = target.with_suffix(target.suffix + '.part')
            temporary.write_text(value, encoding='utf-8')
            temporary.replace(target)
        row.update(status='partial' if partial_note else 'saved', error=partial_note,
                   source_url='\n'.join(sources), collected_at=collected,
                   group_count=sum(p['group_count'] for p in parts),
                   parameter_count=sum(p['parameter_count'] for p in parts),
                   html_path=str(html_path.relative_to(output)), text_path=str(text_path.relative_to(output)),
                   sha256=file_hash(html_path))
    except (OSError, ValueError) as exc:
        row['error'] = str(exc)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('DJI无人机文档'))
    parser.add_argument('--models', nargs='+', help='仅补采指定机型，不修改其他机型的参数索引')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=3)
    args = parser.parse_args()
    output = args.output.resolve()
    products = load_products(output)
    if args.models:
        missing = set(args.models) - {p['slug'] for p in products}
        if missing:
            raise ValueError('不在现有 PDF 索引中的机型: ' + ', '.join(sorted(missing)))
        products = [p for p in products if p['slug'] in args.models]
    catalog, catalog_error = '', ''
    try:
        with fetch(BASE + '/cn/support') as response:
            response.encoding = 'utf-8'
            catalog = response.text
    except (requests.RequestException, OSError, ValueError) as exc:
        catalog_error = str(exc)
        print('支持目录读取失败，将核对标准支持页: ' + catalog_error, flush=True)
    index = output / '技术参数索引.csv'
    rows = {}
    if index.exists():
        with index.open(encoding='utf-8-sig', newline='') as stream:
            rows = {row['slug']: row for row in csv.DictReader(stream)}
    completed = []
    print(f'补采 {len(products)} 个机型的技术参数。', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(capture, product, output, catalog): product for product in products}
        for i, task in enumerate(as_completed(tasks), 1):
            row = task.result()
            rows[row['slug']] = row
            completed.append(row)
            write_csv(index, sorted(rows.values(), key=lambda r: r['slug']), FIELDS)
            print(f'[{i}/{len(tasks)}] {row["slug"]}: {row["status"]}, '
                  f'{row["parameter_count"]} 个参数 {row["error"]}', flush=True)
    summary = {'models_requested': len(products), 'results': dict(Counter(r['status'] for r in completed)),
               'parameters_saved': sum(r['parameter_count'] for r in completed),
               'index_models': len(rows), 'index_results': dict(Counter(r['status'] for r in rows.values())),
               'index_parameters_saved': sum(int(r['parameter_count']) for r in rows.values()),
               'catalog_error': catalog_error, 'output': str(output)}
    (output / '技术参数汇总.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return int(any(r['status'] != 'saved' for r in completed))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (requests.RequestException, OSError, ValueError) as exc:
        print('错误: ' + str(exc), file=sys.stderr)
        sys.exit(1)
