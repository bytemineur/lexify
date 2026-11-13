# -*- coding: utf-8 -*-
"""数据源：各地法院官网「悬赏公告 / 执行公告」栏目。

目标站点均使用同一套 CMS（南方网 NFCMS 站群），栏目页是静态分页
（index.html / index_2.html / ...），详情页为 /content/post_NNNN.html。
不同法院的列表模板在 class 名上有差异，但结构一致：
    <li> ... <a href="...post_xxx.html">标题</a> ... 2026-09-30 ... </li>
因此用「li 块 + 日期就近匹配」的规则通吃，新增法院只需在 config.COURT_SITES
里加一行，无需改代码。

正文提取：按已知容器 class 依次尝试，取命中内容最长的一个；全部落空时
退化为整页去标签文本。
"""
import re

SOURCE = "court"
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
DATE_RE = re.compile(r"(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
POST_RE = re.compile(r'href="([^"]*?/content/post_\d+\.html)"', re.I)
PAGE_RE = re.compile(r"index_(\d+)\.html", re.I)

# 详情页正文容器候选（按优先级）；命中多个时取文本最长者
CONTENT_CLASSES = [
    "news-detail-content-article",   # 深圳中院
    "section_detail",                # 福田
    "TRS_Editor",
    "article-content",
    "content-detail",
    "detail-content",
    "news-content",
    "zoom",
    "text",                          # 南山（<div class='text'>）
]
CLS_ATTR_RE = re.compile(r"""class\s*=\s*(["'])([^"']*)\1""", re.I)

# 正文里出现这些说明抽到的是导航/页脚
NOISE_HINTS = ("版权所有", "ICP备", "网站地图", "无障碍", "主办单位：", "关于我们")


def _clean(text):
    t = TAG_RE.sub("", text or "")
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    t = t.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _abs_url(base, href):
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        p = urllib_parse(base)
        return f"{p[0]}://{p[1]}{href}"
    return base.rstrip("/") + "/" + href.lstrip("/")


def urllib_parse(url):
    m = re.match(r"(https?)://([^/]+)", url)
    return (m.group(1), m.group(2)) if m else ("https", url)


def _block_around(html, idx):
    """取包含 idx 的 <li>...</li> 块；找不到就返回 ±500 字符窗口。"""
    start = html.rfind("<li", 0, idx)
    end = html.find("</li>", idx)
    if start >= 0 and end > 0 and (end - start) < 4000:
        return html[start:end + 5]
    return html[max(0, idx - 500): idx + 500]


def _norm_date(s):
    m = DATE_RE.search(s or "")
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y}-{mo:02d}-{d:02d}"


def parse_list(html, base):
    """解析栏目列表页，返回 [{url, title, publish_date}]（保序去重）。"""
    out, seen = [], set()
    for m in POST_RE.finditer(html):
        url = _abs_url(base, m.group(1))
        if url in seen:
            continue
        seen.add(url)
        block = _block_around(html, m.start())

        # <a> 标签起点：href 之前可能还有 class / title 属性
        a_start = html.rfind("<a", 0, m.start())
        tag = html[a_start:m.end()] if a_start >= 0 else ""
        tm = re.search(r'title="([^"]+)"', tag)

        gt = html.find(">", m.end())
        inner_start = gt + 1 if gt > 0 else m.end()
        inner = html[inner_start: inner_start + 600]
        a_end = inner.lower().find("</a>")
        if a_end > 0:
            inner = inner[:a_end]

        title = _clean(tm.group(1)) if tm else _clean(inner)
        title = re.sub(r"\s+", " ", title).strip()
        # 列表模板常把日期塞在标题块里，清掉尾部日期
        title = re.sub(r"\s*[\[(]?\s*(19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*[\])]?\s*$",
                       "", title).strip()
        if not title or len(title) < 4:
            title = _clean(block)[:120]

        out.append({
            "url": url,
            "title": title,
            "publish_date": _norm_date(block),
        })
    return out


def max_page(html):
    pages = [int(x) for x in PAGE_RE.findall(html)]
    return max(pages) if pages else 1


def parse_content(html):
    """从详情页提取正文文本。

    按 CONTENT_CLASSES 的优先级顺序尝试，某个 class 命中的内容足够长就采用，
    避免"text"这类通用类名把外层导航容器也算进来。
    """
    body = SCRIPT_RE.sub("", html)

    # 先把所有带 class 的容器索引出来：class名 -> [内部HTML]
    index = {}
    for m in re.finditer(r"<(div|article|section|td)\b([^>]*)>", body, re.I):
        cm = CLS_ATTR_RE.search(m.group(2))
        if not cm:
            continue
        tokens = set(cm.group(2).split())      # cm.group(2) = class 属性值
        for cls in CONTENT_CLASSES:
            if cls in tokens:
                frag = _balanced_div(body, m.end(), m.group(1))
                index.setdefault(cls, []).append(frag)

    for cls in CONTENT_CLASSES:
        if cls not in index:
            continue
        best = max((_clean(f) for f in index[cls]), key=len, default="")
        if len(best) >= 150:
            return _strip_boilerplate(best)
        if cls == CONTENT_CLASSES[-1] and len(best) >= 60:
            return _strip_boilerplate(best)

    # 兜底：整页去标签，砍掉明显的导航/页脚噪音
    txt = _clean(body)
    lines = [ln for ln in txt.split("\n")
             if ln and not any(n in ln for n in NOISE_HINTS)]
    return _strip_boilerplate("\n".join(lines))


def _balanced_div(html, start, tag="div"):
    """从 start（起始标签的 > 之后）做标签配平，返回内部 HTML。"""
    depth, i = 1, start
    pat = re.compile(r"<(/?)" + tag + r"\b[^>]*>", re.I)
    while depth > 0:
        m = pat.search(html, i)
        if not m:
            return html[start:]
        depth += -1 if m.group(1) else 1
        i = m.end()
    return html[start:m.start()]


def _strip_boilerplate(text):
    """去掉正文首尾的导航残留（如站点名、面包屑、打印按钮、发布信息行）。"""
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    drop_head = ("打印", "关闭", "分享", "字号", "当前位置", "首页 >", "首页>")
    meta_only = re.compile(r"^(来源|作者|发布时间|浏览|字体|扫一扫)[:：]?\s*$|"
                           r"^(来源|作者|发布时间)[:：]")
    while lines and (len(lines[0]) <= 3 or any(h in lines[0] for h in drop_head)
                     or meta_only.match(lines[0])):
        lines.pop(0)
    while lines and (any(h in lines[-1] for h in drop_head + NOISE_HINTS)
                     or meta_only.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def iter_site(http, site, max_pages=0, verbose=True):
    """遍历某法院的一个栏目，产出统一原始记录 dict。"""
    base = site["base"].rstrip("/")
    prefix = site.get("prefix", "").rstrip("/")
    first_url = f"{base}{prefix}/index.html"

    if verbose:
        print(f"  [{site['court']}] {first_url}")

    try:
        html = http.get(first_url, headers={"Referer": base + "/"})
    except Exception as e:
        print(f"    ! 栏目首页失败: {e}")
        return

    total_pages = max_page(html)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    if verbose:
        print(f"    共 {total_pages} 页")

    items = []
    for pno in range(1, total_pages + 1):
        if pno == 1:
            page_html = html
        else:
            try:
                page_html = http.get(f"{base}{prefix}/index_{pno}.html",
                                     headers={"Referer": first_url})
            except Exception as e:
                print(f"    第 {pno} 页失败: {e}")
                break
        page_items = parse_list(page_html, base)
        if not page_items:
            break
        items.extend(page_items)

    if verbose:
        print(f"    列表 {len(items)} 条")

    for it in items:
        try:
            detail = http.get(it["url"], headers={"Referer": first_url})
        except Exception as e:
            print(f"    ! 详情失败 {it['url']}: {e}")
            continue
        content = parse_content(detail)
        if not content:
            continue
        yield {
            "source": SOURCE,
            "source_id": re.search(r"post_(\d+)", it["url"]).group(1),
            "url": it["url"],
            "court": site["court"],
            "debtor": "",
            "publish_date": it.get("publish_date") or "",
            "notice_type": site.get("notice_type", "执行悬赏公告"),
            "content": content,
            "title": it.get("title", ""),
            "province": site.get("province"),
            "city": site.get("city"),
        }


def to_record(raw):
    """把源站原始 dict 转成 pipeline 需要的统一结构（字段补齐）。"""
    rec = dict(raw)
    rec.setdefault("source", SOURCE)
    rec.setdefault("debtor", "")
    rec.setdefault("notice_type", "")
    return rec
