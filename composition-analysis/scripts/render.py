# -*- coding: utf-8 -*-
"""构图分析 Skill · 渲染器

输入：结构化结果 JSON（由具备读图和文件写入能力的 AI 助手按 21 类判断卡生成）
输出：
  1) 生成标注图（主体框 / 构图线 / 留白区域，视觉估计示意）
  2) 从内置 168 张参考图清单读取案例、作者、来源与逐图授权信息
  3) 重建「构图分析工作台.html」：原网页【只读副本】+ Skill 结果面板 + 内嵌数据/图片
  4) 归档结果 JSON

原网页 构图对比工具.html 只读，绝不覆盖。
用法： python render.py --result "<结果JSON路径>"
"""
import os, sys, io, csv, json, base64, shutil, hashlib, argparse, html as _html, mimetypes
from collections import Counter
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(SKILL, "config.json"), encoding="utf-8"))
ROOT = (os.path.abspath(os.path.join(SKILL, ".."))
        if CFG.get("workspace_root", ".") in ("", ".")
        else CFG["workspace_root"])

def rp(rel):
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel.replace("\\", os.sep))

def uri(path, max_side, quality=84):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = min(1.0, max_side / max(w, h))
    if s < 1.0:
        im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), im

def original_data_uri(path):
    """Embed an author-provided image without recompression (used for a scannable QR)."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return "data:%s;base64," % mime + base64.b64encode(f.read()).decode("ascii")

FONT = None
for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\arial.ttf"]:
    if os.path.exists(fp):
        try:
            FONT = ImageFont.truetype(fp, 18)
            break
        except Exception:
            pass

def P(v, w, h):
    """归一化坐标 -> 像素"""
    return (int(round(v[0] * w)), int(round(v[1] * h)))

def _hit(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

def label(d, xy, text, color, w, h, placed):
    """带半透明白底的标签：深色/浅色背景都可读，钳位在画幅内，
    并自动避让已画过的标签（多条线共起点时不会互相盖住）。
    只改可读性，不改标签文字内容。"""
    t = str(text)
    try:
        bb = d.textbbox((0, 0), t, font=FONT)
        tw, th = bb[2] - bb[0] + 2, bb[3] - bb[1] + 2
    except Exception:
        tw, th = len(t) * 18 + 2, 22
    x = max(3, min(int(xy[0]), w - tw - 9))
    y0 = max(3, min(int(xy[1]), h - th - 9))
    y = y0
    def box(px, py):
        return [px - 4, py - 3, px + tw + 4, py + th + 3]
    rect = box(x, y)
    for _ in range(14):
        if not any(_hit(rect, r) for r in placed):
            break
        y += th + 9
        if y > h - th - 9:
            y = y0
            x = min(x + tw + 14, max(3, w - tw - 9))
        rect = box(x, y)
    d.rectangle(rect, fill=(255, 255, 255, 210))
    d.text((x, y), t, font=FONT, fill=color)
    placed.append(rect)

def annotate(image_path, ann, out_path):
    """画视觉估计示意标注：橙=主体框，蓝=构图线，绿虚线=留白/结构区域"""
    base = Image.open(image_path).convert("RGB")
    w, h = base.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    placed = []   # 已绘制标签矩形，用于自动避让

    # 留白/结构区域（先画，垫在底层）
    for r in (ann.get("regions") or []):
        x0, y0, x1, y1 = P(r["rect"][:2], w, h) + P(r["rect"][2:], w, h)
        d.rectangle([x0, y0, x1, y1], fill=(46, 139, 87, 28), outline=(46, 139, 87, 230), width=3)
        label(d, (x0 + 6, y0 + 5), r.get("label", "结构区域"), (20, 90, 55, 255), w, h, placed)

    # 构图线 + 终点箭头
    for ln in (ann.get("lines") or []):
        a, b = P(ln["from"], w, h), P(ln["to"], w, h)
        d.line([a, b], fill=(31, 111, 168, 245), width=max(3, int(min(w, h) / 260)))
        import math
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        L = max(10, int(min(w, h) / 45))
        for delta in (2.6, -2.6):
            d.line([b, (int(b[0] - L * math.cos(ang + delta / 3)),
                        int(b[1] - L * math.sin(ang + delta / 3)))],
                   fill=(31, 111, 168, 245), width=3)
        # 标签放在线段中点：多条线共起点/共终点时也能各自贴着自己的线
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        label(d, (mid[0] + 6, mid[1] - 26), ln.get("label", "构图线"),
              (15, 70, 120, 255), w, h, placed)

    # 主体框（最后画，最醒目）
    sb = ann.get("subject_box")
    if sb:
        x0, y0, x1, y1 = P(sb[:2], w, h) + P(sb[2:], w, h)
        d.rectangle([x0, y0, x1, y1], outline=(232, 121, 31, 255), width=max(4, int(min(w, h) / 190)))
        label(d, (x0 + 5, y0 - 26), "主体", (190, 85, 10, 255), w, h, placed)

    out = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    out.thumbnail((1000, 1000), Image.LANCZOS)
    out.save(out_path, "JPEG", quality=86, optimize=True)
    return out_path

def validate(data, idx):
    errs = []
    if not isinstance(data.get("images"), list) or not data["images"]:
        errs.append("images 必须是非空数组")
    for x in data.get("images", []):
        tag = x.get("id", "?")
        for k in ["id", "image_path", "main", "aux", "position", "confuse", "layers", "annotations", "cases"]:
            if k not in x:
                errs.append("%s 缺字段 %s" % (tag, k))
        if len(x.get("aux", [])) > 3:
            errs.append("%s 辅助结构超过 3 个" % tag)
        if len(x.get("cases", [])) > 2:
            errs.append("%s 案例超过 2 张" % tag)
        ang = x.get("layers", {}).get("拍摄角度（人工参考）")
        if ang is not None and ang != "未标注":
            errs.append("%s 角度必须写「未标注」（实际：%s）" % (tag, ang))
        if not os.path.isfile(rp(x.get("image_path", ""))):
            errs.append("%s 原图不存在：%s" % (tag, x.get("image_path")))
        for c in x.get("cases", []):
            num = str(c.get("num", "")).lstrip("#")
            if num not in idx:
                errs.append("%s 案例 #%s 不在图库索引里" % (tag, num))
        reverse = x.get("reverse_prompt")
        if reverse is not None:
            if not isinstance(reverse, dict):
                errs.append("%s reverse_prompt 必须是对象或 null" % tag)
            else:
                for k in ["mode", "host_ai", "target_ai", "adaptation_note", "fixed_values",
                          "editable_values", "prompt_gpt_template", "prompt_universal_template",
                          "negative_prompt"]:
                    if k not in reverse:
                        errs.append("%s reverse_prompt 缺字段 %s" % (tag, k))
                if not isinstance(reverse.get("fixed_values", []), list):
                    errs.append("%s reverse_prompt.fixed_values 必须是数组" % tag)
                if not isinstance(reverse.get("editable_values", []), list):
                    errs.append("%s reverse_prompt.editable_values 必须是数组" % tag)
                keys = [v.get("key") for v in reverse.get("editable_values", []) if isinstance(v, dict)]
                if len(keys) != len(set(keys)):
                    errs.append("%s reverse_prompt.editable_values 的 key 必须唯一" % tag)
    return errs

def E(s):
    """HTML 文本/属性转义"""
    return _html.escape("" if s is None else str(s), quote=True)

# 21 类结构编码与网页分类 key 的映射
CLASS_ORDER = [
    ("01", "center", "中心构图"), ("02", "thirds", "三分法"),
    ("03", "symmetry", "对称构图"), ("04", "diagonal", "对角线构图"),
    ("05", "vertical", "垂直线构图"), ("06", "horizontal", "水平线构图"),
    ("07", "curve", "曲线构图"), ("08", "frame", "框架式构图"),
    ("09", "guide", "引导线构图"), ("10", "layers", "前景层次构图"),
    ("11", "contrast", "对比构图"), ("12", "negative", "留白构图"),
    ("13", "fill", "填充画面"), ("14", "pattern", "重复与韵律"),
    ("15", "triangle", "三角形构图"), ("16", "split", "分割构图"),
    ("17", "flatlay", "平铺构图"), ("18", "asym_balance", "非对称均衡构图"),
    ("19", "radial", "放射／径向构图"), ("20", "golden", "黄金比例／黄金螺旋构图"),
    ("21", "perspective", "透视汇聚／消失点构图"),
]
CLASS_BY_CODE = {code: (key, name) for code, key, name in CLASS_ORDER}


def build_library_html(rows):
    """将包内 Commons 授权图与 AI 原创示意图整合为离线图库。"""
    rows = sorted(rows, key=lambda r: str(r.get("display_id", "")))
    kc = Counter(CLASS_BY_CODE.get(str(r.get("display_id", ""))[:2], ("-", ""))[0] for r in rows)
    sc = Counter("commons" if r.get("source_kind") == "Wikimedia Commons" else "ai" for r in rows)
    total = len(rows)
    side = int(CFG.get("library_image_max_side", 720))
    quality = int(CFG.get("library_image_quality", 82))

    o = ['<div class="libBar">']
    o.append('<div class="libRow"><input id="libQ" placeholder="搜索编号 / 类别 / 画面证据 / 作者 / 许可…">'
             '<span id="libCount">显示 %d / %d 张</span></div>' % (total, total))
    o.append('<div class="libRow"><span class="libLab">素材来源</span>'
             '<button class="libSb on" data-s="all" type="button">全部 %d</button>'
             '<button class="libSb" data-s="commons" type="button">Commons 公开许可 %d</button>'
             '<button class="libSb" data-s="ai" type="button">项目 AI 原创 %d</button></div>'
             % (total, sc["commons"], sc["ai"]))
    fb = ['<button class="libFb on" data-f="all" type="button">全部 %d</button>' % total]
    for code, key, name in CLASS_ORDER:
        fb.append('<button class="libFb" data-f="%s" type="button">%s %d</button>'
                  % (E(key), E(name), kc.get(key, 0)))
    o.append('<div class="libRow"><span class="libLab">主构图</span>' + "".join(fb) + '</div>')
    o.append('</div>')
    o.append('<div class="libGrid" id="libGrid">')

    for r in rows:
        num = str(r.get("display_id", ""))
        code = num[:2]
        key, category = CLASS_BY_CODE.get(code, ("-", r.get("category", "未分类")))
        source_kind = r.get("source_kind", "")
        is_commons = source_kind == "Wikimedia Commons"
        flag = "commons" if is_commons else "ai"
        src_label = "Wikimedia Commons · 公开许可" if is_commons else "项目 AI 原创教学示意"
        src_cls = "libSrcCommons" if is_commons else "libSrcAI"
        img_path = os.path.join(SKILL, "assets", "gallery", os.path.basename(r["asset"]))
        pic, _ = uri(img_path, side, quality)
        author = r.get("author") or "作者未标注"
        license_name = r.get("license") or "许可未标注"
        page_url = r.get("page_url") or ""
        license_url = r.get("license_url") or ""
        search = " ".join(str(v or "") for v in [num, category, r.get("evidence"), r.get("tags"), author,
                         license_name, source_kind, r.get("filename"), r.get("description"), page_url, license_url]).lower()
        o.append('<div class="libCard" id="lib-%s" data-k="%s" data-src="%s" data-num="%s" data-search="%s">'
                 % (E(num), E(key), flag, E(num), E(search)))
        o.append('<img class="libImg" loading="lazy" src="%s" alt="%s · %s" tabindex="0" role="button" aria-label="打开大图：%s">'
                 % (pic, E(num), E(category), E(num)))
        o.append('<div class="libBd"><div class="libHd"><b>%s</b><span class="libNo">#%s</span></div>'
                 % (E(category), E(num)))
        o.append('<div class="libSub"><span class="%s">%s</span> · %s × %s</div>'
                 % (src_cls, E(src_label), E(r.get("width", "—")), E(r.get("height", "—"))))
        o.append('<p class="libEv">%s</p>' % E(r.get("evidence", "")))
        if is_commons:
            o.append('<div class="libMini">作者：%s</div>' % E(author))
            o.append('<div class="libLicense">许可：%s</div>' % E(license_name))
            if page_url.startswith("http"):
                o.append('<a class="libSrc" href="%s" target="_blank" rel="noopener">查看原作品来源页 ↗</a>' % E(page_url))
            if license_url.startswith("http"):
                o.append('<a class="libLicenseLink" href="%s" target="_blank" rel="noopener">查看许可条款 ↗</a>' % E(license_url))
        else:
            o.append('<div class="libMini">来源：OpenAI 图像生成</div>')
            o.append('<div class="libAIInfo">项目原创 AI 教学示意；不标作 CC 授权图片。</div>')
        o.append('</div></div>')
    o.append('</div>')
    return "\n".join(o), total, sc

ENGINE_NOTE = (
    "以下为构图分析 Skill 按「21 类判断卡」做的视觉分析结果。"
    "网页内置引擎仍只有 17 类规则；第 18–21 类（非对称均衡、放射／径向、黄金比例／黄金螺旋、透视汇聚／消失点）"
    "由 Skill 视觉判读给出，不代表网页引擎已支持。"
    "关闭面板后看到的检测结果是「规则检测参考」（旧引擎 17 类），两套结果不混用。"
)
DEFAULT_TRIGGER_NOTE = (
    "本页是静态本地网页，没有连接 AI 服务的接口。本页「放图／拖入／粘贴」只运行旧 17 类规则检测；"
    "21 类视觉判读与提示词反推要在支持图片输入的 AI 对话里附图调用 Skill。"
)
FOOTER = (
    "原网页 构图对比工具.html 未被修改，本页是它的新版本副本；"
    "网页旧引擎仍只有 17 类规则，21 类深度判读由 AI Skill 对话完成；"
    "参考图库共 123 张 Wikimedia Commons 公开许可图片与 45 张项目 AI 原创教学示意；"
    "每张 Commons 图片须按页面标示的具体许可使用，遵守署名或相同方式共享等条件；"
    "AI 示意图不标作 CC 授权，图库数量不代表识别准确率。"
)
# 静态徽标：写进原网页 17 类结果区，保证任何情况下都明确标注「规则检测参考」
RULE_BADGE = (
    '<div id="ruleRefBadge"><b>规则检测参考（旧引擎 17 类 · 非 Skill 判读）</b>'
    '<span>下面这区的候选结果由本页「放图／拖入／粘贴」触发的旧规则引擎给出，'
    '例如「中心构图、引导线、前景层次」；它和右下角「Skill 判读结果」面板里的 '
    '21 类视觉判读结论是两套，不能混用。</span></div>\n        '
)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=os.path.join(rp(CFG["results_dir"]), "最新结果.json"))
    ap.add_argument("--no-open-note", action="store_true")
    a = ap.parse_args()

    result_path = a.result if os.path.isabs(a.result) else rp(a.result)
    data = json.load(open(result_path, encoding="utf-8"))
    gallery_path = os.path.join(SKILL, "assets", "gallery", "gallery_manifest.json")
    rows = json.load(open(gallery_path, encoding="utf-8"))
    idx = {str(r["display_id"]): r for r in rows}

    errs = validate(data, idx)
    if errs:
        print("结果 JSON 校验未通过：")
        for e in errs: print("  -", e)
        sys.exit(2)

    results_dir = rp(CFG["results_dir"])
    os.makedirs(results_dir, exist_ok=True)
    asset_dir = os.path.join(results_dir, "_标注图")
    os.makedirs(asset_dir, exist_ok=True)

    only = set(CFG["skill_only_keys"])
    out_images = []
    for n, x in enumerate(data["images"], 1):
        src = rp(x["image_path"])
        orig_uri, _ = uri(src, 1000)
        ann_path = os.path.join(asset_dir, "%s_标注.jpg" % x["id"].replace("/", "_"))
        annotate(src, x.get("annotations") or {}, ann_path)
        ann_uri, _ = uri(ann_path, 1000, 86)

        cases = []
        for c in x.get("cases", []):
            num = str(c["num"]).lstrip("#")
            r = idx[num]
            fp = os.path.join(SKILL, "assets", "gallery", os.path.basename(r["asset"]))
            c_uri, _ = uri(fp, 420, 80)
            is_commons = r.get("source_kind") == "Wikimedia Commons"
            cases.append({
                "num": "#" + num, "main": r.get("category", ""), "subject": r.get("source_kind", ""),
                "source_type": "Wikimedia Commons 公开许可" if is_commons else "项目 AI 原创教学示意",
                "author": r.get("author", ""), "license": r.get("license", ""),
                "source_url": r.get("page_url", ""), "license_url": r.get("license_url", ""),
                "similar": c.get("similar", ""), "different": c.get("different", ""), "uri": c_uri,
            })

        m = dict(x["main"])
        m["skill_only"] = m.get("key") in only
        out_images.append({
            "id": x["id"], "pixels": x.get("pixels", ""), "desc": x.get("desc", ""),
            "orig_uri": orig_uri, "annot_uri": ann_uri,
            "main": m, "aux": x.get("aux", []), "position": x.get("position", {}),
            "confuse": x.get("confuse", []), "uncertain": x.get("uncertain", ""),
            "layers": x.get("layers", {}), "cases": cases,
            "case_note": x.get("case_note", ""),
            "annotation_note": x.get("annotation_note", ""),
            "reverse_prompt": x.get("reverse_prompt"),
        })

    panel_data = {
        "title": data.get("title", "构图分析 · 21 类判断卡"),
        "generated_at": "生成时间 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engine_note": ENGINE_NOTE,
        "trigger_note": data.get("trigger_note", DEFAULT_TRIGGER_NOTE),
        "batch_note": data.get("batch_note", ""),
        "label_note": data.get("label_note", ""),
        "input_note": data.get("input_note", ""),
        "footer": FOOTER + " 本次结果 JSON：" + os.path.basename(result_path),
        "images": out_images,
        "usage": data.get("usage"),
    }
    payload = json.dumps(panel_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # script[application/json] 的 raw text 里 "<!--" 会改变 HTML 解析状态；用合法 JSON 转义还原
    if "<!--" in payload:
        payload = payload.replace("<!--", "<\\u0021--")

    panel = open(os.path.join(SKILL, "assets", "skill_panel.html"), encoding="utf-8").read()
    if (panel.count("__SKILL_DATA__") != 1 or panel.count("__LIBRARY_HTML__") != 1
            or panel.count("__AUTHOR_AVATAR_URI__") != 1 or panel.count("__GUIDE_IMAGE_URI__") != 2
            or panel.count("__ABOUT_WECHAT_URI__") != 1 or panel.count("__ABOUT_DOG_URI__") != 1):
        sys.exit("面板模板占位符异常（数据、图库、头像、导览或关于我图片占位符数量不正确）")
    lib_html, lib_total, lib_sources = build_library_html(rows)
    avatar_path = os.path.join(SKILL, "assets", "author-avatar.jpg")
    if not os.path.isfile(avatar_path):
        sys.exit("作者头像文件缺失：%s" % avatar_path)
    with open(avatar_path, "rb") as f:
        avatar_uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
    about_dir = os.path.join(SKILL, "assets", "about")
    about_wechat_path = os.path.join(about_dir, "wechat-search-longtouzi.png")
    about_dog_path = os.path.join(about_dir, "eighteen-pug-swing.jpg")
    for image_path in (about_wechat_path, about_dog_path):
        if not os.path.isfile(image_path):
            sys.exit("关于我页面图片缺失：%s" % image_path)
    about_wechat_uri = original_data_uri(about_wechat_path)
    about_dog_uri = original_data_uri(about_dog_path)
    guide_row = next((r for r in rows if str(r.get("display_id", "")) == "20-07"), None)
    if not guide_row:
        sys.exit("导览页示例图片缺失：找不到图库编号 20-07")
    guide_path = os.path.join(SKILL, "assets", "gallery", os.path.basename(guide_row["asset"]))
    guide_uri, _ = uri(guide_path, 1200, 86)
    injection = (panel.replace("__SKILL_DATA__", payload)
                 .replace("__LIBRARY_HTML__", lib_html)
                 .replace("__AUTHOR_AVATAR_URI__", avatar_uri)
                 .replace("__GUIDE_IMAGE_URI__", guide_uri)
                 .replace("__ABOUT_WECHAT_URI__", about_wechat_uri)
                 .replace("__ABOUT_DOG_URI__", about_dog_uri)
                 .replace("__LIBRARY_TOTAL__", str(lib_total))
                 .replace("__COMMONS_TOTAL__", str(lib_sources["commons"]))
                 .replace("__AI_TOTAL__", str(lib_sources["ai"])))

    # 静态写入工作台默认展开。模板默认显示「最新判读」；无结果时显示空状态，图库仍可由导航打开。
    panel_tag = '<div id="skillPanel" aria-label="构图分析工作台">'
    if injection.count(panel_tag) != 1:
        sys.exit("面板模板结构异常：找不到唯一的 skillPanel 标签")
    injection = injection.replace(
        panel_tag,
        '<div id="skillPanel" class="on" aria-label="构图分析工作台">', 1)

    engine_path = rp(CFG["engine_html"])
    engine = open(engine_path, encoding="utf-8", newline="").read()
    # 原始检测页保留不动；只在工作台副本的页头加返回图库入口。
    ref_button = '<button class="btn sm" id="btnRefLibrary" type="button">参考图库</button>\n  '
    kb_marker = '<button class="btn sm" id="btnKb">'
    if 'id="btnRefLibrary"' not in engine:
        if engine.count(kb_marker) != 1:
            sys.exit("原网页结构异常：找不到唯一的规则说明按钮")
        engine = engine.replace(kb_marker, ref_button + kb_marker, 1)
    if engine.count("</body>") != 1:
        sys.exit("原网页结构异常：找不到唯一的 </body>")
    wb = engine.replace("</body>", injection + "\n</body>")

    # 静态写入「规则检测参考」徽标：直接放进原网页 17 类结果区，
    # paintSide() 只清 metaMain/candMain/noteMain 的 innerHTML，不会删这个兄弟节点。
    meta_marker = '<div class="meta" id="metaMain"></div>'
    if wb.count(meta_marker) != 1:
        sys.exit("原网页结构异常：找不到唯一的 metaMain 容器")
    wb = wb.replace(meta_marker, RULE_BADGE + meta_marker, 1)

    wb_path = rp(CFG["workbench_html"])
    with open(wb_path, "w", encoding="utf-8", newline="") as f:
        f.write(wb)

    latest = os.path.join(results_dir, "最新结果.json")
    result_abs, latest_abs = os.path.abspath(result_path), os.path.abspath(latest)
    result_hash = hashlib.sha256(open(result_path, "rb").read()).hexdigest()
    # 只把新的内容归档一次，避免每次重渲染都造出一份看似新的历史批次。
    if result_abs != latest_abs:
        known_hashes = set()
        for name in os.listdir(results_dir):
            if not name.lower().endswith(".json"):
                continue
            candidate = os.path.join(results_dir, name)
            if os.path.abspath(candidate) == result_abs or not os.path.isfile(candidate):
                continue
            with open(candidate, "rb") as f:
                known_hashes.add(hashlib.sha256(f.read()).hexdigest())
        if result_hash not in known_hashes:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_path = os.path.join(results_dir, "%s.json" % stamp)
            if os.path.exists(archive_path):
                archive_path = os.path.join(results_dir, "%s_%s.json" % (stamp, result_hash[:8]))
            shutil.copy2(result_path, archive_path)
        shutil.copy2(result_path, latest)

    md5 = hashlib.md5(open(engine_path, "rb").read()).hexdigest()
    print("结果 JSON :", result_path)
    print("工作台    :", wb_path, "(%d bytes)" % os.path.getsize(wb_path))
    print("图片张数  :", len(out_images), "| 案例", sum(len(x["cases"]) for x in out_images), "张")
    print("参考图库  : 同一个 HTML 内嵌 %d 张（Commons %d / AI 原创 %d）" % (len(rows), lib_sources["commons"], lib_sources["ai"]))
    print("Skill 独有类别标记:", sum(1 for x in out_images if x["main"]["skill_only"]))
    print("原网页 md5（只读基线）:", md5)
    print("标注图目录:", asset_dir)
    print("完成。工作台默认进入最新判读；图片分析页的上传只运行旧 17 类规则；21 类视觉判读需由 AI 对话调用 Skill。")
    print("需要 21 类 Skill 判读时，在支持该 Skill 的 AI 对话中附图调用；AI 服务未连接到静态网页。")

if __name__ == "__main__":
    main()
