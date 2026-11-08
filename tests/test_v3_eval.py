"""scripts/eval.py `ungrounded`: evidence checked against the facts code loads (offline, no LLM)."""

import importlib.util
import json

from resolve import config, db
from tests.fakes_v2 import CUID, mock_profile, new_db, paid_profile, services, triage_dict

spec = importlib.util.spec_from_file_location("eval_script", config.ROOT / "scripts" / "eval.py")
eval_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_script)


def test_ungrounded_flags_only_lines_not_in_the_facts(http, tmp_path):
    conn = new_db(tmp_path)
    prof = paid_profile()
    mock_profile(http, prof)
    product = prof["access_levels"][0]["store_product_id"]
    result = triage_dict(evidence=[f"product: {product}", "price: $999.99"])
    ticket = db.get_ticket(conn, "t-001")
    assert ticket["customer_user_id"] == CUID
    out = eval_script.ungrounded(services(conn, None), conn, ticket, json.loads(json.dumps(result)))
    assert out == ["price: $999.99"]
