"""「下载海报」的行为断言。

对照 2026-11-04 的需求: 主窗口「定点截图」右边加一个「下载海报」按钮,
弹窗里能选文档 (csv/xlsx) → 分析 (看前 5 条 + 选海报链接字段) → 下载。

跑法:
    QT_QPA_PLATFORM=offscreen python docs/superpowers/specs/2026-11-04-poster-assertions.py
退出码 0 = 全部通过；非 0 = 有失败。
"""
import os
import shutil
import sys
import tempfile
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _ROOT)

_tmpdirs: list[str] = []


def mkdtemp(prefix: str = 'vdcheck_poster_') -> str:
    path = tempfile.mkdtemp(prefix=prefix)
    _tmpdirs.append(path)
    return path


def cleanup_tmpdirs() -> None:
    for path in _tmpdirs:
        shutil.rmtree(path, ignore_errors=True)
    _tmpdirs.clear()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import video_core  # noqa: E402
import video_qt  # noqa: E402

_failures: list[str] = []
msgbox_log: list[tuple[str, str, str]] = []


def stub_msgbox(kind: str):
    def _stub(parent, title, text, *args, **kwargs):
        msgbox_log.append((kind, title, text))
        return QMessageBox.Ok
    return _stub


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "✓" if condition else "✗"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        _failures.append(line)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 文档样本

CSV_HEADERS = ['partname', '海报链接', '时长']
CSV_ROWS = [
    ['星穹计划', 'http://x.test/a.jpg', '90'],
    ['天启纪元', 'http://x.test/b.jpg', '95'],
    ['星穹计划', 'http://x.test/c.jpg', '100'],
    ['苍山洱海', 'http://x.test/d.jpg', '88'],
    ['北纬三十度', 'http://x.test/e.jpg', '77'],
    ['南风知我意', 'http://x.test/f.jpg', '66'],
    ['长夜行', 'http://x.test/g.jpg', '55'],
    ['春日迟迟', 'http://x.test/h.jpg', '44'],
]


def write_csv(path: str, encoding: str = 'utf-8-sig', bom: bool = True) -> str:
    lines = [','.join(CSV_HEADERS)]
    for row in CSV_ROWS:
        lines.append(','.join(row))
    data = '\n'.join(lines) + '\n'
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write(data)
    return path


def _col_letter(idx: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA'。"""
    s = ''
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _xml_escape(text: str) -> str:
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def make_xlsx(path: str, headers: list[str], rows: list[list[str]],
              use_shared: bool = True) -> str:
    """造一个最小可读的 xlsx (自己写 zip + XML, 不引第三方库)。

    use_shared=True  → 字符串走 sharedStrings.xml (Excel/WPS 的常见做法)
    use_shared=False → 字符串走 inlineStr (t="inlineStr")
    """
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

    all_cells = [headers] + rows
    shared: list[str] = []
    index: dict[str, int] = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(shared)
            shared.append(text)
        return index[text]

    sheet_rows = []
    for r, cells in enumerate(all_cells, start=1):
        parts = []
        for c, value in enumerate(cells):
            ref = f'{_col_letter(c)}{r}'
            if use_shared:
                parts.append(f'<c r="{ref}" t="s"><v>{sid(value)}</v></c>')
            else:
                parts.append(
                    f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{r}">' + ''.join(parts) + '</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{ns}"><sheetData>'
        + ''.join(sheet_rows) + '</sheetData></worksheet>')

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>')

    rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>',
    ]
    if use_shared:
        rels.append(
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/sharedStrings" '
            'Target="sharedStrings.xml"/>')
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">' + ''.join(rels) + '</Relationships>')

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        + ('<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
           'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
           if use_shared else '')
        + '</Types>')

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('xl/workbook.xml', workbook_xml)
        z.writestr('xl/_rels/workbook.xml.rels', rels_xml)
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        if use_shared:
            items = ''.join(f'<si><t>{_xml_escape(s)}</t></si>' for s in shared)
            z.writestr('xl/sharedStrings.xml',
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       f'<sst xmlns="{ns}" count="{len(shared)}" '
                       f'uniqueCount="{len(shared)}">{items}</sst>')
    return path


def make_jpeg(path: str, payload: bytes = b'\xff\xd8\xff\xe0' + b'x' * 200) -> str:
    with open(path, 'wb') as f:
        f.write(payload)
    return path


# ---------------------------------------------------------------- 纯逻辑

def test_read_csv():
    section('读 csv')
    d = mkdtemp()
    path = write_csv(os.path.join(d, '清单.csv'))
    doc = video_core.read_tabular_document(path)
    check('表头读对', doc.headers == CSV_HEADERS, f"got={doc.headers!r}")
    check('行数读对', len(doc.rows) == 8, f"got={len(doc.rows)}")
    check('第一行数据读对', doc.rows[0] == CSV_ROWS[0], f"got={doc.rows[0]!r}")
    check('utf-8 BOM 被吃掉', doc.headers[0] == 'partname',
          f"got={doc.headers[0]!r}")

    path_gbk = os.path.join(d, 'gbk.csv')
    with open(path_gbk, 'w', encoding='gbk', newline='') as f:
        f.write('partname,海报链接\n星穹计划,http://x.test/a.jpg\n')
    doc2 = video_core.read_tabular_document(path_gbk)
    check('GBK 编码读对', doc2.rows[0][0] == '星穹计划',
          f"got={doc2.rows[0][0]!r}")

    doc3 = video_core.read_tabular_document(path, has_header=False)
    check('无表头模式: 列名兜底', doc3.headers[0].startswith('字段'),
          f"got={doc3.headers[0]!r}")
    check('无表头模式: 第一行也当数据', len(doc3.rows) == 9,
          f"got={len(doc3.rows)}")


def test_read_xlsx():
    section('读 xlsx')
    d = mkdtemp()
    shared_path = make_xlsx(os.path.join(d, 'shared.xlsx'),
                             CSV_HEADERS, CSV_ROWS, use_shared=True)
    doc = video_core.read_tabular_document(shared_path)
    check('sharedStrings: 表头读对', doc.headers == CSV_HEADERS,
          f"got={doc.headers!r}")
    check('sharedStrings: 行数读对', len(doc.rows) == 8, f"got={len(doc.rows)}")
    check('sharedStrings: 数据读对', doc.rows[1] == CSV_ROWS[1],
          f"got={doc.rows[1]!r}")

    inline_path = make_xlsx(os.path.join(d, 'inline.xlsx'), CSV_HEADERS,
                             CSV_ROWS, use_shared=False)
    doc2 = video_core.read_tabular_document(inline_path)
    check('inlineStr: 表头读对', doc2.headers == CSV_HEADERS,
          f"got={doc2.headers!r}")
    check('inlineStr: 数据读对', doc2.rows[0] == CSV_ROWS[0],
          f"got={doc2.rows[0]!r}")

    # 数字单元格: 整数别写成 '90.0'
    num_path = make_xlsx(os.path.join(d, 'num.xlsx'), ['partname', '时长'],
                         [['甲', '90'], ['乙', '95.5']])
    doc3 = video_core.read_tabular_document(num_path)
    check('数字单元格: 整数不带小数点', doc3.rows[0][1] == '90',
          f"got={doc3.rows[0][1]!r}")
    check('数字单元格: 小数保留', doc3.rows[1][1] == '95.5',
          f"got={doc3.rows[1][1]!r}")


def test_bad_format():
    section('不支持的格式')
    d = mkdtemp()
    xls = os.path.join(d, '老表.xls')
    with open(xls, 'wb') as f:
        f.write(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    try:
        video_core.read_tabular_document(xls)
        check('.xls 应当报错', False, '没抛异常')
    except video_core.DocumentFormatError as exc:
        check('.xls 报错并提示另存为', '另存为' in str(exc) and '.xlsx' in str(exc),
              f"got={str(exc)!r}")

    other = os.path.join(d, '文档.docx')
    with open(other, 'wb') as f:
        f.write(b'not a table')
    try:
        video_core.read_tabular_document(other)
        check('.docx 应当报错', False, '没抛异常')
    except video_core.DocumentFormatError:
        check('.docx 报错', True)

    missing = os.path.join(d, '不存在.csv')
    try:
        video_core.read_tabular_document(missing)
        check('文件不存在应当报错', False, '没抛异常')
    except (video_core.DocumentFormatError, FileNotFoundError, OSError):
        check('文件不存在报错', True)


def test_guess_columns():
    section('自动猜字段')
    headers = CSV_HEADERS
    rows = CSV_ROWS
    check('海报链接字段: 按列名猜',
          video_core.guess_link_columns(headers, rows) == [1],
          f"got={video_core.guess_link_columns(headers, rows)}")
    check('命名来源字段: 按列名猜',
          video_core.guess_partname_column(headers, rows) == 0,
          f"got={video_core.guess_partname_column(headers, rows)}")

    # 列名里没有线索 → 看值是不是像链接
    h2 = ['甲', '乙']
    r2 = [['x', 'http://x.test/1.jpg']]
    check('海报链接字段: 按值猜',
          video_core.guess_link_columns(h2, r2) == [1],
          f"got={video_core.guess_link_columns(h2, r2)}")

    # 全无线索 → 一列都不认 (让用户自己勾, 不偷偷拿错的列去下载)
    h3 = ['甲', '乙']
    r3 = [['x', 'y']]
    check('海报链接字段: 无线索 → 一个都不认',
          video_core.guess_link_columns(h3, r3) == [],
          f"got={video_core.guess_link_columns(h3, r3)}")
    check('命名来源字段: 无线索退回第一列',
          video_core.guess_partname_column(h3, r3) == 0)

    # 中文列名
    h4 = ['剧名', '海报']
    r4 = [['甲', 'http://x.test/1.jpg']]
    check('海报链接字段: 中文列名「海报」',
          video_core.guess_link_columns(h4, r4) == [1],
          f"got={video_core.guess_link_columns(h4, r4)}")
    check('命名来源字段: 中文列名「剧名」',
          video_core.guess_partname_column(h4, r4) == 0,
          f"got={video_core.guess_partname_column(h4, r4)}")

    # **多个链接字段** (2026-11-04 用户要求)
    h5 = ['partname', '竖版海报', '横版海报', '时长']
    r5 = [['甲', 'http://x.test/v.jpg', 'http://x.test/h.jpg', '90']]
    check('海报链接字段: 多个都认出来',
          video_core.guess_link_columns(h5, r5) == [1, 2],
          f"got={video_core.guess_link_columns(h5, r5)}")
    h6 = ['甲', '乙', '丙']
    r6 = [['x', 'http://x.test/1.jpg', 'http://x.test/2.jpg']]
    check('海报链接字段: 按值认出多列',
          video_core.guess_link_columns(h6, r6) == [1, 2],
          f"got={video_core.guess_link_columns(h6, r6)}")
    # 列名像但值不是链接 → 不认 (免得把「图片说明」当下图链接)
    h7 = ['海报', '备注']
    r7 = [['这是一段说明', 'x']]
    check('海报链接字段: 列名像但值不是链接 → 不认',
          video_core.guess_link_columns(h7, r7) == [],
          f"got={video_core.guess_link_columns(h7, r7)}")


def test_multi_link_jobs():
    section('多个链接字段 → 多个任务')
    rows = [['甲', 'http://x/v.jpg', 'http://x/h.jpg'],
            ['乙', 'http://x/v2.jpg', '']]
    jobs = video_core.build_poster_jobs(
        rows, 0, [(1, '竖版海报'), (2, '横版海报')], '{partname}-{imgcol}')
    check('每个 (行, 链接字段) 一个任务', len(jobs) == 3, f"got={len(jobs)}")
    check('文件名里的 imgcol 取**列名**',
          jobs[0] == ('http://x/v.jpg', '甲-竖版海报')
          and jobs[1] == ('http://x/h.jpg', '甲-横版海报'),
          f"got={jobs[:2]!r}")
    check('空链接的格子跳过',
          jobs[2] == ('http://x/v2.jpg', '乙-竖版海报'), f"got={jobs[2]!r}")


def test_render_filename():
    section('命名规则渲染')
    name = video_core.render_filename('{partname}-{imgcol}', '星穹计划', '海报链接')
    check('花括号模板', name == '星穹计划-海报链接', f"got={name!r}")
    name2 = video_core.render_filename('[partname]-[imgcol]', '星穹计划', '海报链接')
    check('方括号模板 (用户原文的写法)', name2 == '星穹计划-海报链接',
          f"got={name2!r}")
    name3 = video_core.render_filename('{partname}', '星穹计划', '海报链接')
    check('只有 partname', name3 == '星穹计划', f"got={name3!r}")

    bad = video_core.render_filename('{partname}:{imgcol}', 'a/b:c', 'x')
    check('非法字符换成 _', ':' not in bad and '/' not in bad, f"got={bad!r}")

    empty = video_core.render_filename('{partname}', '', '海报链接')
    check('空名字不返回空串', bool(empty), f"got={empty!r}")


def test_extension():
    section('扩展名推断')
    check('URL 带后缀',
          video_core.extension_for_url('http://x.test/a.jpg') == '.jpg')
    check('URL 带 png',
          video_core.extension_for_url('http://x.test/a.PNG') == '.png')
    check('URL 没后缀 + Content-Type',
          video_core.extension_for_url('http://x.test/a', 'image/png') == '.png')
    check('URL 没后缀也没 Content-Type → 兜底 .jpg',
          video_core.extension_for_url('http://x.test/a') == '.jpg')
    check('URL 的查询串不算后缀',
          video_core.extension_for_url('http://x.test/a?x=1.jpg') == '.jpg')


def test_download_poster():
    section('下载一张海报')
    d = mkdtemp()
    src = make_jpeg(os.path.join(d, 'a.jpg'))
    out = mkdtemp()
    url = 'file://' + src.replace(os.sep, '/')
    from pathlib import Path
    url = Path(src).as_uri()
    path = video_core.download_poster(url, out, '星穹计划-海报链接')
    check('文件落盘了', os.path.isfile(path), f"path={path!r}")
    check('文件名 = 命名规则 + 扩展名',
          os.path.basename(path) == '星穹计划-海报链接.jpg',
          f"got={os.path.basename(path)!r}")
    with open(path, 'rb') as f:
        data = f.read()
    with open(src, 'rb') as f:
        expect = f.read()
    check('内容一致', data == expect)

    try:
        video_core.download_poster('file:///no/such/file.jpg', out, 'x')
        check('下载失败应当抛异常', False, '没抛异常')
    except Exception:
        check('下载失败抛异常', True)


def test_default_dir():
    section('海报地址默认值')
    got = video_core.poster_default_dir('/work/清单.xlsx', '星穹计划')
    expect = os.path.join(os.path.dirname(os.path.abspath('/work/清单.xlsx')),
                          '星穹计划-poster')
    check('= 文档所在目录/<partname>-poster', got == expect, f"got={got!r}")

    jobs = video_core.build_poster_jobs(
        CSV_ROWS, 0, [(1, '海报链接')], '{partname}-{imgcol}')
    check('jobs: 数量', len(jobs) == 8, f"got={len(jobs)}")
    check('jobs: 文件名按模板', jobs[0] == ('http://x.test/a.jpg', '星穹计划-海报链接'),
          f"got={jobs[0]!r}")
    check('jobs: 空链接的行跳过',
          video_core.build_poster_jobs([['a', '']], 0, [(1, 'c')],
                                       '{partname}') == [])


# ---------------------------------------------------------------- 界面

def test_dialog():
    section('弹窗行为')
    d = mkdtemp()
    csv_path = write_csv(os.path.join(d, '清单.csv'))
    dlg = video_qt.PosterDialog(None)
    dlg.show()
    app.processEvents()
    try:
        check('未分析前「开始下载」禁用', not dlg.download_btn.isEnabled())
        check('模板默认 {partname}-{imgcol}',
              dlg.template_edit.text() == '{partname}-{imgcol}',
              f"got={dlg.template_edit.text()!r}")

        # ---- 版面 (2026-11-04 用户要求) ----
        check('「开始分析」在「文档」这一行、「浏览」之后',
              abs(dlg.analyze_btn.y() - dlg.doc_browse_btn.y()) < 5
              and dlg.analyze_btn.x() > dlg.doc_browse_btn.x(),
              f"analyze=({dlg.analyze_btn.x()},{dlg.analyze_btn.y()}) "
              f"browse=({dlg.doc_browse_btn.x()},{dlg.doc_browse_btn.y()})")
        check('「命名来源字段」在「海报地址」前面 (同一行)',
              abs(dlg.part_combo.y() - dlg.out_edit.y()) < 5
              and dlg.part_combo.x() < dlg.out_edit.x(),
              f"part=({dlg.part_combo.x()},{dlg.part_combo.y()}) "
              f"out=({dlg.out_edit.x()},{dlg.out_edit.y()})")

        dlg.doc_edit.setText(csv_path)
        dlg._on_analyze()
        check('分析后「开始下载」可用', dlg.download_btn.isEnabled())
        check('海报链接字段: 所有列都列出来',
              [name for name, _ in dlg.link_checks] == CSV_HEADERS,
              f"got={[name for name, _ in dlg.link_checks]!r}")
        checked = [name for name, cb in dlg.link_checks if cb.isChecked()]
        check('海报链接字段: 自动认出「海报链接」并勾上',
              checked == ['海报链接'], f"got={checked!r}")
        check('命名来源字段下拉: 默认选中「partname」',
              dlg.part_combo.currentText() == 'partname',
              f"got={dlg.part_combo.currentText()!r}")
        check('预览只显示前 5 行', dlg.model.rowCount() == 5,
              f"got={dlg.model.rowCount()}")
        check('预览列数 = 字段数', dlg.model.columnCount() == 3,
              f"got={dlg.model.columnCount()}")
        check('预览第一行 = 文档第一条数据',
              dlg.model.rows[0] == CSV_ROWS[0], f"got={dlg.model.rows[0]!r}")
        check('海报地址自动填 = 文档目录/<partname首行>-poster',
              dlg.out_edit.text() == os.path.join(d, '星穹计划-poster'),
              f"got={dlg.out_edit.text()!r}")
        check('状态行报出分析结果',
              '8' in dlg.status_label.text(), f"got={dlg.status_label.text()!r}")

        # ---- 改了命名来源 → 海报地址同步跟着变 ----
        dlg.part_combo.setCurrentIndex(2)          # '时长' → 首行值 '90'
        check('改「命名来源字段」→ 海报地址**同步**跟着变',
              dlg.out_edit.text() == os.path.join(d, '90-poster'),
              f"got={dlg.out_edit.text()!r}")
        dlg.part_combo.setCurrentIndex(0)
        check('改回「partname」→ 地址也跟着回来',
              dlg.out_edit.text() == os.path.join(d, '星穹计划-poster'),
              f"got={dlg.out_edit.text()!r}")

        # ---- 勾多个链接字段 ----
        dlg.link_checks[2][1].setChecked(True)      # 把「时长」也勾上
        check('可以勾多个海报链接字段',
              [n for n, cb in dlg.link_checks if cb.isChecked()]
              == ['海报链接', '时长'])

        # 改了文档路径 → 之前的分析作废 (否则拿旧文档去下载)
        dlg.doc_edit.setText(csv_path + '.csv')
        check('手改文档路径 → 分析作废 (开始下载禁用)',
              not dlg.download_btn.isEnabled())
        check('手改文档路径 → 预览清空', dlg.model.rowCount() == 0)
        check('手改文档路径 → 勾选框清空',
              dlg.link_checks == [], f"got={dlg.link_checks!r}")

        # 改「首行为字段名」→ 读法变了 → 分析作废
        dlg.doc_edit.setText(csv_path)
        dlg._on_analyze()
        check('重新分析后又开始下载可用', dlg.download_btn.isEnabled())
        dlg.header_check.setChecked(False)
        check('改「首行为字段名」→ 分析作废 (开始下载禁用)',
              not dlg.download_btn.isEnabled())
    finally:
        dlg.close()


def test_dialog_download():
    section('下载全链路 (file://)')
    d = mkdtemp()
    out = mkdtemp()
    img1 = make_jpeg(os.path.join(d, 'a.jpg'), b'AAA' + b'x' * 50)
    img2 = make_jpeg(os.path.join(d, 'b.jpg'), b'BBB' + b'x' * 50)
    img3 = make_jpeg(os.path.join(d, 'c.jpg'), b'CCC' + b'x' * 50)
    from pathlib import Path
    csv_path = os.path.join(d, '清单.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('partname,竖版海报,横版海报\n')
        f.write('星穹计划,%s,%s\n' % (Path(img1).as_uri(), Path(img3).as_uri()))
        f.write('天启纪元,%s,\n' % Path(img2).as_uri())

    dlg = video_qt.PosterDialog(None)
    try:
        dlg.doc_edit.setText(csv_path)
        dlg._on_analyze()
        checked = [n for n, cb in dlg.link_checks if cb.isChecked()]
        check('两个海报链接字段都自动认出并勾上',
              checked == ['竖版海报', '横版海报'], f"got={checked!r}")
        dlg.out_edit.setText(out)
        dlg._on_download()
        # 等下载线程跑完
        deadline = time.time() + 20
        while dlg._downloading and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        check('下载结束', not dlg._downloading)
        check('成功 3 张 (2 行 × 2 字段, 有一个空格跳过)',
              dlg._download_ok == 3, f"got={dlg._download_ok}")
        check('失败 0 张', dlg._download_failed == 0,
              f"got={dlg._download_failed}")
        names = sorted(os.listdir(out))
        check('落盘文件名按命名规则 (imgcol = 各列自己的列名)',
              names == ['天启纪元-竖版海报.jpg', '星穹计划-横版海报.jpg',
                        '星穹计划-竖版海报.jpg'],
              f"got={names!r}")
        check('状态行报成功数', '成功' in dlg.status_label.text(),
              f"got={dlg.status_label.text()!r}")
    finally:
        dlg.close()


def test_main_window_button():
    section('主窗口按钮')
    win = video_qt.MainWindow()
    win.show()
    app.processEvents()
    try:
        check('主窗口有「下载海报」按钮', hasattr(win, 'poster_btn'))
        check('「下载海报」在「定点截图」右边',
              win.poster_btn.x() > win.snapshot_btn.x(),
              f"poster={win.poster_btn.x()} snapshot={win.snapshot_btn.x()}")
        check('「下载海报」文字',
              win.poster_btn.text() == '下载海报',
              f"got={win.poster_btn.text()!r}")
        # 不需要先扫描 —— 没扫描结果也能开弹窗
        opened = []

        class _FakeDialog:
            def __init__(self, *args, **kwargs):
                opened.append(True)

            def exec(self):
                return 0

        real = video_qt.PosterDialog
        video_qt.PosterDialog = _FakeDialog
        try:
            win._on_poster_btn_click()
        finally:
            video_qt.PosterDialog = real
        check('没有扫描结果也能开弹窗', bool(opened))
    finally:
        win.close()


def main() -> int:
    orig_warning = QMessageBox.warning
    orig_critical = QMessageBox.critical
    orig_question = QMessageBox.question
    QMessageBox.warning = stub_msgbox('warning')
    QMessageBox.critical = stub_msgbox('critical')
    QMessageBox.question = stub_msgbox('question')
    try:
        test_read_csv()
        test_read_xlsx()
        test_bad_format()
        test_guess_columns()
        test_multi_link_jobs()
        test_render_filename()
        test_extension()
        test_download_poster()
        test_default_dir()
        test_dialog()
        test_dialog_download()
        test_main_window_button()
    finally:
        QMessageBox.warning = orig_warning
        QMessageBox.critical = orig_critical
        QMessageBox.question = orig_question
        cleanup_tmpdirs()

    print()
    if _failures:
        print(f"✗ {len(_failures)} 条断言失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == '__main__':
    sys.exit(main())
