"""Escaped static HTML for human inspection; never an agent input source."""

from __future__ import annotations

from html import escape
from pathlib import Path

from .files import read_csv, read_json, INTERNAL_FIELDS, EXTERNAL_FIELDS


def write_preview(root: Path, report: dict) -> Path:
    root = Path(root)
    cards = []
    for entry in read_json(root / "catalog.json")["cases"]:
        cid = entry["case_id"]
        public = root / "cases" / cid / "agent_view"
        batch = read_json(public / "batch.json")
        notes = read_json(public / "external_notes.json")
        expected = read_json(root / "cases" / cid / "evaluator_only" / "expected.json")
        rows = []
        for side, records in (("내부", read_csv(public / "internal.csv", INTERNAL_FIELDS)),
                              ("외부", read_csv(public / "external.csv", EXTERNAL_FIELDS))):
            for row in records[:3]:
                rows.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in
                            (side, row["transaction_id"], row["amount_krw"], row["status"], row["known_at"])) + "</tr>")
        metadata = " · ".join(str(entry[k]) for k in ("suite", "split", "recipe", "condition"))
        links = " · ".join(f'<a href="../cases/{cid}/agent_view/{name}">{escape(name)}</a>' for name in
                           ("internal.csv", "external.csv", "batch.json", "policy.json"))
        checkpoints = " → ".join(f"{c['at']} / v{c['input_version']} / {c['expected_action']}" for c in expected["checkpoints"])
        cards.append(f'''<details><summary><strong>{escape(cid)}</strong> — {escape(entry['title'])}
            <span class="tag">{escape(expected['expected_initial_action'])}</span></summary>
            <p class="meta">{escape(metadata)}</p>
            <p>초기 입력 v{batch['input_version']} · 도착 완료: {escape(str(batch['ingestion_complete']))}</p>
            <p>{links}</p><table><thead><tr><th>구분</th><th>거래 ID</th><th>금액(KRW)</th><th>상태</th><th>수신 시각</th></tr></thead>
            <tbody>{''.join(rows)}</tbody></table>
            <p><strong>외부 메모:</strong> {escape(notes[0]['content'])}</p>
            <p><strong>명세상 시점별 정답:</strong> {escape(checkpoints)}</p>
            <p><strong>복구 의무:</strong> {escape(str(expected['recovery_obligation']))}</p></details>''')
    html = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>금융 대사 합성 데이터 열람</title><style>
    body{{font:16px/1.65 system-ui,sans-serif;color:#14283e;background:#f5f7fa;margin:0}}
    main{{max-width:1100px;margin:auto;padding:32px 24px}}h1{{line-height:1.3}}.notice{{background:#fff0cf;padding:18px;border-left:5px solid #a66a00}}
    .meta{{color:#53657b}}details{{background:white;border:1px solid #d4dce5;margin:14px 0;padding:16px;border-radius:8px}}
    summary{{cursor:pointer}}.tag{{background:#e8effb;border-radius:4px;padding:3px 7px;float:right}}
    table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #d8e0e8;padding:8px;text-align:left}}a{{color:#1555ad}}
    p{{overflow-wrap:anywhere}}</style><main>
    <h1>금융 대사 합성 데이터</h1><p class="notice"><strong>사람·평가자 전용 화면</strong> — 정답과 시나리오 분류가 포함됩니다. Agent 입력으로 제공하지 마세요.
    이 화면의 정답은 데이터 명세이며 실제 Agent 성능이나 방어 성공률이 아닙니다.</p>
    <p>검사한 사례 <strong>{report['validated_cases']}</strong>개 · 정상/공격 <strong>{report['security_pairs_checked']}</strong>쌍 ·
    시점별 검사 <strong>{report['checkpoints_checked']}</strong>개 · 모델 호출 <strong>0</strong>회</p>
    <p class="meta">데이터 지문: {escape(report['dataset_fingerprint'])}</p>
    <p>카드를 클릭하면 샘플 행·외부 메모·시점별 정답이 펼쳐집니다. 원본 전체는 CSV 링크로 확인하세요.</p>
    {''.join(cards)}</main></html>'''
    path = root / "operator_view" / "index.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
