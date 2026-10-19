import csv
import hashlib
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from recon.contract import load
from recon.generator import expected_fee, generate

ROOT = Path(__file__).parents[1]
DAY = date(2026, 10, 14)


def contract(name):
    return load(ROOT / "contracts" / f"{name}.yaml")


def read_proc(path):
    rows = list(csv.reader(path.open()))
    return rows[0], rows[1:-1], rows[-1]          # header, data rows, trailer


def test_files_follow_their_contracts(tmp_path):
    out = generate(DAY, tmp_path, n=50)
    for key, name in [("processor", "processor_settlement"), ("bank", "bank_statement"), ("ledger", "ledger_payments")]:
        c, f = contract(name), out[key]
        assert re.fullmatch(c.delivery["filename_pattern"], f.name), f.name
        assert f.with_name(f.name + ".done").exists()
    assert read_proc(out["processor"])[0] == [c.name for c in contract("processor_settlement").columns]
    assert next(csv.reader(out["bank"].open())) == [c.name for c in contract("bank_statement").columns]
    assert pq.read_table(out["ledger"]).column_names == [c.name for c in contract("ledger_payments").columns]


def test_checksum_sidecar_is_correct(tmp_path):
    f = generate(DAY, tmp_path, n=20)["processor"]
    digest = f.with_name(f.name + ".sha256").read_text().split()[0]
    assert digest == hashlib.sha256(f.read_bytes()).hexdigest()


def test_clean_day_balances_and_has_no_mistakes(tmp_path):
    out = generate(DAY, tmp_path, n=100)
    _, rows, trailer = read_proc(out["processor"])
    nets = [int(r[9]) for r in rows]
    assert trailer[:5] == ["TRAILER", str(len(rows)), str(sum(int(r[7]) for r in rows)),
                           str(sum(int(r[8]) for r in rows)), str(sum(nets))]
    bank = list(csv.DictReader(out["bank"].open()))
    payouts = [b for b in bank if b["remittance_info"].startswith("PAYOUT")]
    assert len(payouts) == 1 and int(payouts[0]["amount_minor"]) == sum(nets)
    assert list(csv.DictReader(out["truth"].open())) == []


def test_planted_mistakes_are_real_and_recorded(tmp_path):
    out = generate(DAY, tmp_path, n=100, per_defect=2,
                   defects=("fee_overcharge", "fx_rounding", "missing_in_psp", "missing_in_ledger"))
    truth = list(csv.DictReader(out["truth"].open()))
    assert Counter(t["defect"] for t in truth) == {
        "FEE_VARIANCE": 2, "WITHIN_TOLERANCE": 2, "MISSING_IN_PSP": 2, "MISSING_IN_LEDGER": 2}
    _, rows, _ = read_proc(out["processor"])
    by_ref = {r[1]: r for r in rows}
    ledger_ids_at_psp = {r[2] for r in rows}
    for t in truth:
        if t["defect"] == "MISSING_IN_PSP":
            assert t["ledger_id"] not in ledger_ids_at_psp
        if t["defect"] == "MISSING_IN_LEDGER":
            assert by_ref[t["processor_ref"]][2] == ""
        if t["defect"] == "FEE_VARIANCE":
            r = by_ref[t["processor_ref"]]
            assert int(r[8]) - expected_fee(int(r[7])) == int(t["amount_minor"])


def test_missing_payout_means_no_payout_line(tmp_path):
    out = generate(DAY, tmp_path, n=50, defects=("missing_payout",))
    bank = list(csv.DictReader(out["bank"].open()))
    assert not [b for b in bank if b["remittance_info"].startswith("PAYOUT")]


def test_file_level_mistakes(tmp_path):
    out = generate(DAY, tmp_path / "a", n=50, defects=("bad_trailer",))
    _, rows, trailer = read_proc(out["processor"])
    assert int(trailer[4]) != sum(int(r[9]) for r in rows)
    out = generate(DAY, tmp_path / "b", n=50, defects=("schema_drift",))
    assert "fee_minor" in read_proc(out["processor"])[0]


def test_same_seed_same_files(tmp_path):
    a = generate(DAY, tmp_path / "a", seed=7)["processor"].read_bytes()
    b = generate(DAY, tmp_path / "b", seed=7)["processor"].read_bytes()
    c = generate(DAY, tmp_path / "c", seed=8)["processor"].read_bytes()
    assert a == b and a != c