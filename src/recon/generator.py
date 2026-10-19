"""Fake data generator.

Creates one business day of files for the three sources (shop ledger, card company, bank),
plants known mistakes on purpose, and writes a truth file listing every planted mistake.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

FEE_RATE = Decimal("0.029")
ROW_DEFECTS = ("missing_in_psp", "fee_overcharge", "fx_rounding")
DEFECTS = ROW_DEFECTS + ("missing_in_ledger", "missing_payout", "bad_trailer", "schema_drift")

PROCESSOR_COLUMNS = ["settlement_batch_id", "psp_reference", "merchant_reference", "record_type",
                     "transaction_date", "settlement_date", "currency", "gross_amount_minor",
                     "fee_amount_minor", "net_amount_minor", "payout_id"]
BANK_COLUMNS = ["statement_id", "bank_ref", "account_id", "value_date", "booking_date", "currency",
                "amount_minor", "counterparty", "remittance_info"]
TRUTH_COLUMNS = ["defect", "ledger_id", "processor_ref", "amount_minor", "note"]


def expected_fee(gross: int) -> int:
    """2.9% of gross, rounded half-up to a whole cent."""
    return int((Decimal(gross) * FEE_RATE).quantize(Decimal("1"), ROUND_HALF_UP))


def write_atomic(path: Path, data: bytes) -> None:
    """Write to a temporary name, then rename, so nobody can ever read half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def deliver(path: Path, data: bytes, sidecar: bool = True) -> None:
    """Deliver like a real partner: data file first, then fingerprint, then the .done sticker LAST."""
    write_atomic(path, data)
    if sidecar:
        digest = hashlib.sha256(data).hexdigest()
        write_atomic(path.with_name(path.name + ".sha256"), f"{digest}  {path.name}\n".encode())
    write_atomic(path.with_name(path.name + ".done"), b"")


def to_csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode()


def generate(day: date, out: Path, n: int = 200, seed: int = 42,
             defects: tuple[str, ...] = (), per_defect: int = 2) -> dict[str, Path]:
    rng = random.Random(f"{seed}-{day}")          # same seed + same day = exactly the same files
    d, yymmdd = day.strftime("%Y%m%d"), day.strftime("%y%m%d")
    settle_day, bank_day = day + timedelta(days=1), day + timedelta(days=2)
    bd = bank_day.strftime("%Y%m%d")
    batch_id, payout_id = f"B{yymmdd}", f"PO-{d}"
    truth: list[list] = []

    # 1) Shop ledger: what our shop recorded -------------------------------------------
    ledger = []
    for i in range(1, n + 1):
        refund = rng.random() < 0.15
        amount = rng.randint(500, 50_000) * (-1 if refund else 1)
        ts = datetime.combine(day, time(), timezone.utc) + timedelta(seconds=rng.randint(0, 86_399))
        ledger.append({
            "extract_id": f"EX{yymmdd}", "ledger_txn_id": f"L{d}{i:05d}", "payment_id": f"PAY{d}{i:05d}",
            "event_type": "REFUND" if refund else "CAPTURE", "event_ts_utc": ts,
            "currency": "USD", "amount_minor": amount, "psp_reference": f"P{d}{i:05d}",
        })

    # Pick distinct sales to spoil for each row-level mistake
    captures = [r for r in ledger if r["event_type"] == "CAPTURE"]
    wanted = per_defect * sum(x in defects for x in ROW_DEFECTS)
    if wanted > len(captures):
        raise ValueError(f"need {wanted} captures to plant mistakes, only have {len(captures)}; raise --payments")
    pool = rng.sample(captures, k=wanted)

    skip, fee_bump, gross_bump = set(), {}, {}
    if "missing_in_psp" in defects:
        for _ in range(per_defect):
            r = pool.pop()
            skip.add(r["ledger_txn_id"])
            truth.append(["MISSING_IN_PSP", r["ledger_txn_id"], "", r["amount_minor"], "pending until T+2, then a break"])
    if "fee_overcharge" in defects:
        for _ in range(per_defect):
            r = pool.pop()
            fee_bump[r["ledger_txn_id"]] = extra = rng.choice([25, 30, 50, 100])
            truth.append(["FEE_VARIANCE", r["ledger_txn_id"], r["psp_reference"], extra, "fee above 2.9%"])
    if "fx_rounding" in defects:
        for _ in range(per_defect):
            r = pool.pop()
            gross_bump[r["ledger_txn_id"]] = diff = rng.choice([-2, -1, 1, 2])
            truth.append(["WITHIN_TOLERANCE", r["ledger_txn_id"], r["psp_reference"], diff, "should still match (tier 2)"])

    # 2) Card company settlement: what the processor says ------------------------------
    proc = []
    for r in ledger:
        lid = r["ledger_txn_id"]
        if lid in skip:
            continue
        gross = r["amount_minor"] + gross_bump.get(lid, 0)
        fee = (expected_fee(gross) if r["event_type"] == "CAPTURE" else 0) + fee_bump.get(lid, 0)
        proc.append([batch_id, r["psp_reference"], lid, r["event_type"], day.isoformat(),
                     settle_day.isoformat(), "USD", gross, fee, gross - fee, payout_id])
    if "missing_in_ledger" in defects:
        for j in range(1, per_defect + 1):
            gross = rng.randint(500, 50_000)
            fee = expected_fee(gross)
            ref = f"PX{d}{j:05d}"
            proc.append([batch_id, ref, "", "CAPTURE", day.isoformat(), settle_day.isoformat(),
                         "USD", gross, fee, gross - fee, payout_id])
            truth.append(["MISSING_IN_LEDGER", "", ref, gross, "settled but never booked"])

    sum_gross = sum(r[7] for r in proc)
    sum_fee = sum(r[8] for r in proc)
    sum_net = sum(r[9] for r in proc)
    trailer_net = sum_net
    proc_name = f"psp_alpha_settlement_{d}_{batch_id}_v1.csv"
    if "bad_trailer" in defects:
        trailer_net += 100
        truth.append(["QUARANTINE", "", proc_name, 100, "trailer net total is wrong"])
    header = list(PROCESSOR_COLUMNS)
    if "schema_drift" in defects:
        header[header.index("fee_amount_minor")] = "fee_minor"
        truth.append(["QUARANTINE", "", proc_name, 0, "column fee_amount_minor renamed to fee_minor"])
    trailer = ["TRAILER", len(proc), sum_gross, sum_fee, trailer_net] + [""] * (len(header) - 5)
    proc_path = out / "sftp/psp_alpha/outbound/settlement" / proc_name
    deliver(proc_path, to_csv(header, proc + [trailer]))

    # 3) Bank statement: what actually arrived -----------------------------------------
    bank_rows = []
    if "missing_payout" in defects:
        truth.append(["MISSING_IN_BANK", "", payout_id, sum_net, "payout never arrived"])
    else:
        bank_rows.append([f"ST-{bd}", f"BANK-{bd}-0001", "ACC-001", bank_day.isoformat(), bank_day.isoformat(),
                          "USD", sum_net, "PSP ALPHA LTD", f"PAYOUT {payout_id} BATCH {batch_id}"])
    # A normal non-card line (rent): the pipeline must learn to ignore it
    bank_rows.append([f"ST-{bd}", f"BANK-{bd}-0002", "ACC-001", bank_day.isoformat(), bank_day.isoformat(),
                      "USD", -250_000, "CITY OFFICES LLC", "RENT OCTOBER"])
    bank_path = out / "sftp/bank_one/outbound/statements" / f"bank_one_statement_{bd}_ST-{bd}_v1.csv"
    deliver(bank_path, to_csv(BANK_COLUMNS, bank_rows))

    # 4) Shop ledger file (Parquet keeps types like timestamp and integer) -------------
    schema = pa.schema([("extract_id", pa.string()), ("ledger_txn_id", pa.string()), ("payment_id", pa.string()),
                        ("event_type", pa.string()), ("event_ts_utc", pa.timestamp("us", tz="UTC")),
                        ("currency", pa.string()), ("amount_minor", pa.int64()), ("psp_reference", pa.string())])
    table = pa.Table.from_pylist(ledger, schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    ledger_path = out / "s3_inbox/shop_ledger" / f"shop_ledger_payments_{d}_EX{yymmdd}_v1.parquet"
    deliver(ledger_path, sink.getvalue().to_pybytes(), sidecar=False)

    # 5) Truth file: the answer key ------------------------------------------------------
    truth_path = out / "truth" / f"{d}_truth.csv"
    write_atomic(truth_path, to_csv(TRUTH_COLUMNS, truth))

    return {"ledger": ledger_path, "processor": proc_path, "bank": bank_path, "truth": truth_path}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate one fake business day of settlement files")
    p.add_argument("--date", required=True, type=date.fromisoformat, help="business date, e.g. 2026-10-14")
    p.add_argument("--payments", type=int, default=200)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--defects", default="", help="comma list of: " + ",".join(DEFECTS))
    p.add_argument("--per-defect", type=int, default=2)
    p.add_argument("--out", type=Path, default=Path("data"))
    a = p.parse_args(argv)
    defects = tuple(x.strip() for x in a.defects.split(",") if x.strip())
    unknown = set(defects) - set(DEFECTS)
    if unknown:
        p.error(f"unknown defects: {sorted(unknown)}")
    for name, path in generate(a.date, a.out, a.payments, a.seed, defects, a.per_defect).items():
        print(f"{name:>9}: {path}")


if __name__ == "__main__":
    main()