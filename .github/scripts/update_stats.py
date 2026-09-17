#!/usr/bin/env python3
"""
refreshes the usage numbers in index.html from jsdelivr, npm, packagist and github.

every number on the page sits in an element like
    <strong data-stat="packagist-total stefangabos/zebra_image">127K</strong>
where the first word is the metric and the rest are the packages whose values get summed.

metrics:
    jsdelivr-month   jsdelivr cdn requests over the last 30 days, packages as npm/<name> or gh/<user>/<repo>
    npm-year         npm downloads over the last year
    packagist-total  all-time packagist installs
    github-stars     stargazers across the listed <user>/<repo> repositories

ranking metrics take a user (and a language) instead of packages and are not summed:
    codersrank-world-rank    <user> <Language>   worldwide position on codersrank, shown as #745
    codersrank-world-pool    <user> <Language>   developers ranked worldwide for that language, shown as 107K
    codersrank-country-rank  <user> <Language>   position in the user's country, shown as #7
    codersrank-world-top     <user> <Language>   the rounded "top N%" codersrank shows on the profile page, shown as 3%
    gitstar-rank             <user>              position among github users by stars on gitstar ranking, shown as #9,732

usage: python3 .github/scripts/update_stats.py [path to index.html]
a metric that fails to fetch keeps the number already on the page.
rows inside each <ul class="projects"> are then sorted by their metric, largest first, rows without one last.
"""
import html
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAGE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'index.html')
PATTERN = re.compile(r'(<(\w+)[^>]*\bdata-stat="([^"]+)"[^>]*>)([^<]*)(</\2>)')


def fetch_json(url):
    headers = {'User-Agent': 'stefangabos.github.io stats'}
    if 'api.github.com' in url and os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def fetch_text(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (stefangabos.github.io stats)'}), timeout=30) as response:
        return response.read().decode('utf-8', 'replace')


def ranking(metric, arguments, cache):
    if metric == 'codersrank-world-top':
        user, language = arguments
        key = ('codersrank-profile', user)
        if key not in cache:
            page = re.sub(r'<script.*?</script>|<style.*?</style>', '', fetch_text('https://profile.codersrank.io/user/' + user), flags=re.S)
            cache[key] = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', page)))
        top = re.search(r'(?<![A-Za-z])' + re.escape(language) + r' [\d.,]+ exp\. Top ([\d.]+%) out of [\d.]+K? Worldwide', cache[key])
        return top.group(1)
    if metric.startswith('codersrank-'):
        user, language = arguments
        key = ('codersrank', user)
        if key not in cache:
            cache[key] = fetch_json('https://api.codersrank.io/v2/users/%s/languages' % user)
        data = cache[key][language]
        if metric == 'codersrank-world-rank':
            return '#{:,}'.format(data['world_wide_rank'])
        if metric == 'codersrank-world-pool':
            return compact(data['world_wide_all'])
        if metric == 'codersrank-country-rank':
            return '#{:,}'.format(data['country_rank'])
    if metric == 'gitstar-rank':
        user, = arguments
        key = ('gitstar', user)
        if key not in cache:
            cache[key] = fetch_text('https://gitstar-ranking.com/' + user)
        rank = re.search(r'>\s*Rank\s*</div>\s*<div[^>]*>\s*([\d,]+)\s*<', cache[key])
        return '#{:,}'.format(int(rank.group(1).replace(',', '')))
    raise ValueError('unknown metric ' + metric)


def value(metric, package):
    if metric == 'jsdelivr-month':
        kind, name = package.split('/', 1)
        return fetch_json('https://data.jsdelivr.com/v1/stats/packages/%s/%s?period=month' % (kind, name))['hits']['total']
    if metric == 'npm-year':
        return fetch_json('https://api.npmjs.org/downloads/point/last-year/' + package)['downloads']
    if metric == 'packagist-total':
        return fetch_json('https://packagist.org/packages/%s.json' % package)['package']['downloads']['total']
    if metric == 'github-stars':
        return fetch_json('https://api.github.com/repos/' + package)['stargazers_count']
    raise ValueError('unknown metric ' + metric)


def compact(number):
    # 5586269 -> 5.6M, 148632 -> 149K, 3424 -> 3.4K
    for size, suffix in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K')):
        if number >= size:
            scaled = number / size
            text = ('%.1f' % scaled) if scaled < 10 else ('%d' % round(scaled))
            return text.replace('.0', '') + suffix
    return str(number)


def expand(text):
    # 5.6M -> 5600000, used only to order rows
    multipliers = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}
    text = text.strip()
    if text and text[-1] in multipliers:
        return float(text[:-1]) * multipliers[text[-1]]
    return float(text) if text else 0.0


def sort_rows(html):
    def sort_list(match):
        rows = re.findall(r'[ \t]*<li>.*?</li>\n', match.group(2), re.S)
        if not rows or ''.join(rows) != match.group(2):
            return match.group(0)

        def weight(row):
            metric = re.search(r'class="metric"[^>]*><strong data-stat="[^"]*">([^<]*)</strong>', row)
            return -expand(metric.group(1)) if metric else float('inf')

        return match.group(1) + ''.join(sorted(rows, key=weight)) + match.group(3)

    return re.sub(r'(<ul class="projects">\n)(.*?)([ \t]*</ul>)', sort_list, html, flags=re.S)


def main():
    with open(PAGE, encoding='utf-8') as handle:
        html = handle.read()
    cache = {}
    failures = []

    def replace(match):
        metric, *packages = match.group(3).split()
        try:
            if metric.startswith(('codersrank-', 'gitstar-')):
                return match.group(1) + ranking(metric, packages, cache) + match.group(5)
            total = 0
            for package in packages:
                key = (metric, package)
                if key not in cache:
                    cache[key] = value(metric, package)
                total += cache[key]
            return match.group(1) + compact(total) + match.group(5)
        except Exception as error:
            failures.append('%s %s: %s' % (metric, ' '.join(packages), error))
            return match.group(0)

    updated = sort_rows(PATTERN.sub(replace, html))
    if updated != html:
        with open(PAGE, 'w', encoding='utf-8') as handle:
            handle.write(updated)
    print('%d stats checked, %d fetched values, %d failed' % (len(PATTERN.findall(html)), len(cache), len(failures)))
    for failure in failures:
        print('  kept old value for ' + failure)
    return 0


if __name__ == '__main__':
    sys.exit(main())
