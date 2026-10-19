import csv
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

G = Path(__file__).parent / "golden"
FEE_RATE = Decimal("0.029")

def read(name):
    with open(G / name, newline="") as f:
        return list(csv.DictReader(f))

ledger = read("ledger.csv")
proc_all = read("processor_settlement.csv")
proc = [r for r in proc_all if r["settlement_batch_id"] != "TRAILER"]
trailer = next(r for r in proc_all if r["settlement_batch_id"] == "TRAILER")
bank = read("bank_statement.csv")
expected = read("expected_outcomes.csv")
i = int

def expected_fee(gross: int) -> int:
    return int((Decimal(gross) * FEE_RATE).quantize(Decimal("1"), ROUND_HALF_UP))

def test_trailer_matches_rows():
    vals = list(trailer.values())   # TRAILER,row_count,sum_gross,sum_fee,sum_net
    assert i(vals[1]) == len(proc)
    assert i(vals[2]) == sum(i(r["gross_amount_minor"]) for r in proc)
    assert i(vals[3]) == sum(i(r["fee_amount_minor"]) for r in proc)
    assert i(vals[4]) == sum(i(r["net_amount_minor"]) for r in proc)

def test_net_equals_gross_minus_fee():
    for r in proc:
        assert i(r["net_amount_minor"]) == i(r["gross_amount_minor"]) - i(r["fee_amount_minor"])

def test_only_p2_has_fee_variance():
    variances = {r["psp_reference"]: i(r["fee_amount_minor"]) - expected_fee(i(r["gross_amount_minor"]))
                 for r in proc if r["record_type"] == "CAPTURE"}
    assert {k: v for k, v in variances.items() if v} == {"P2": 30}

def test_batch_net_equals_bank_credit():
    assert sum(i(r["net_amount_minor"]) for r in proc) == i(bank[0]["amount_minor"])

def test_ledger_side_balances():
    amt = {r["ledger_txn_id"]: i(r["amount_minor"]) for r in ledger}
    matched = sum(amt[e["ledger_id"]] for e in expected if e["ledger_id"] and e["status"] == "MATCHED")
    pending = sum(amt[e["ledger_id"]] for e in expected if e["status"] == "PENDING")
    assert sum(amt.values()) == matched + pending

def test_processor_side_balances():
    gross = {r["psp_reference"]: i(r["gross_amount_minor"]) for r in proc}
    matched = sum(gross[e["processor_id"]] for e in expected if e["processor_id"] in gross and e["status"] == "MATCHED")
    breaks = sum(gross[e["processor_id"]] for e in expected if e["status"] == "BREAK")
    assert sum(gross.values()) == matched + breaks