"""Canonical printable summary shared by download and delivery."""
from hashlib import sha256
from html import escape
from decimal import Decimal

from .reports import TZ

_EFFORT_LABELS = {"development": "研发", "implementation": "实施", "management": "管理", "unclassified": "未分类／待澄清"}
_STYLES = """
    @page { size: A4 portrait; margin: 12mm; }
    * { box-sizing: border-box; }
    body { margin: 0; color: #172437; font: 10pt/1.35 system-ui, "Microsoft YaHei", sans-serif; }
    main { overflow-wrap: anywhere; }
    header { border-bottom: 2px solid #345275; padding-bottom: 2.5mm; margin-bottom: 3mm; }
    h1 { font-size: 19pt; line-height: 1.2; margin: 0 0 1.5mm; }
    h2 { font-size: 11pt; margin: 0 0 1mm; }
    p, dl, dd { margin: 0; }
    section { margin: 2mm 0; break-inside: avoid; }
    ul { margin: 0; padding-inline-start: 5mm; }
    dt, .note { color: #40546c; }
    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1.5mm 3mm;
      padding: 2mm 3mm; border: 1px solid #a4b1c1; }
    .metrics dd { font-weight: 600; }
    footer { border-top: 1px solid #a4b1c1; margin-top: 3mm; padding-top: 2mm; font-size: 8pt; }
    @media screen { main { max-width: 210mm; margin: 0 auto; padding: clamp(16px, 4vw, 12mm); } }
    @media screen and (max-width: 540px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
"""


def _excerpt(value: str, limit: int) -> tuple[str, bool]:
    clean = ' '.join(value.split())
    shortened = len(clean) > limit
    return escape(clean[:limit]) + ('…' if shortened else ''), shortened


def _section(title: str, entries: list[str], empty: str, *, limit: int = 3) -> str:
    selected = [_excerpt(entry, 72) for entry in entries[:limit]]
    omitted = len(entries) - len(selected)
    shortened = sum(was_shortened for _, was_shortened in selected)
    notices = []
    if omitted:
        notices.append(f'省略 {omitted} 项')
    if shortened:
        notices.append(f'{shortened} 项已节录')
    body = ('<ul>' + ''.join(f'<li>{text}</li>' for text, _ in selected) + '</ul>'
            if selected else f'<p class="note">{escape(empty)}</p>')
    if notices:
        body += f'<p class="note">{" · ".join(notices)}，详见原报告。</p>'
    return f'<section><h2>{escape(title)}</h2>{body}</section>'


def _entry_text(entry: dict) -> str:
    text = next((entry[key] for key in ('summary', 'name', 'description') if isinstance(entry.get(key), str)), '')
    claim = {'completion': '完工申报', 'progress': '进展申报'}.get(str(entry.get('claim', '')), '')
    resource = entry.get('milestoneId') or entry.get('requirementId') or entry.get('riskId')
    return ' · '.join(value for value in (text, claim, resource) if value) or '详见原报告条目'


def _clock(instant) -> str:
    return instant.astimezone(TZ).strftime('%Y/%m/%d %H:%M:%S')


def _lamp(color):
    return {'green': '绿', 'yellow': '黄', 'red': '红', 'untrusted': '不可信', 'unavailable': '不可用'}.get(color, '不可用')


def render(report, fields) -> tuple[str, bytes, str]:
    title = f'项目周报 {report.period} · v{report.version_no}'
    filename = f'项目周报-{report.period}-v{report.version_no}.html'
    basis = report.weekly_basis
    health = basis.get('health', {}) if basis else {}
    verified_count = basis['verifiedPeriodPostingCount'] if basis else 0
    verified = f"{basis['verifiedPeriodPersonDays']}（{verified_count} 笔）" if verified_count else ('无核验流水' if basis else '不可用')
    metrics = [('时间线', _lamp(health.get('timeLine'))), ('成本线', _lamp(health.get('costLine'))),
               ('质量线', _lamp(health.get('qualityLine'))), ('申报人天', fields.effort_person_days),
               ('当周核验人天净额', verified), ('整体健康', _lamp(health.get('health')))]
    metric_html = ''.join(f'<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>' for label, value in metrics)
    sections = [('里程碑进展', fields.milestone_progress, '本期无里程碑申报'),
                ('需求进展', fields.requirement_progress, '本期无需求进展记录'),
                ('风险与阻塞', fields.risks_and_blockers, '本期无风险或阻塞记录')]
    section_html = ''.join(_section(label, [_entry_text(item) for item in entries], empty) for label, entries, empty in sections)
    parts = fields.effort_breakdown or []
    if parts:
        lines = [f'{_EFFORT_LABELS[part.category]} {Decimal(part.personDays):.2f} 人天 · {part.basis}' for part in parts]
        section_html += _section('本期申报人天分类', lines, '', limit=4)
    narrative, shortened = _excerpt(report.narrative or '', 150)
    if narrative:
        notice = '<p class="note">补充说明已节录，详见原报告。</p>' if shortened else ''
        section_html += f'<section><h2>补充说明</h2><p>{narrative}</p>{notice}</section>'
    basis_html = '<p>无生成依据快照。</p>'
    if basis:
        from .weekly_basis import WeeklyBasis

        view = WeeklyBasis.model_validate(basis)
        basis_html = (f'<p>生成依据（上海）{_clock(view.asOf)} · 规则 {escape(view.ruleVersion)}</p>'
                      f'<p>依据摘要 <code>{escape(view.sourceHash)}</code></p>')
        omitted = [(name, count) for name, count in (('需求', view.omittedRequirements), ('风险', view.omittedRisks),
                                                     ('已核验 Fact', view.omittedVerifiedFacts)) if count]
        if omitted:
            basis_html += '<p>生成时未纳入摘要：' + ' · '.join(f'{name} {count} 项' for name, count in omitted) + '，详见原报告。</p>'
    html = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>{escape(title)}</title>
  <style>{_STYLES}  </style>
</head>
<body><main>
  <header>
    <h1>{escape(title)}</h1>
    <p>{report.period_start} 至 {report.period_end}（不含结束日） · 修订 {report.revision}</p>
    <p>已发布 · 确认时间（上海）{_clock(report.confirmed_at)}</p>
  </header>
  <dl class="metrics">{metric_html}</dl>
  {section_html}
  <footer>
    <p>项目 ID <code>{report.project_id}</code> · 报告 ID <code>{report.id}</code></p>
    <p>内容摘要 <code>{escape(report.content_hash)}</code></p>
    {basis_html}
  </footer>
</main></body>
</html>'''.encode('utf-8')
    return filename, html, sha256(html).hexdigest()
