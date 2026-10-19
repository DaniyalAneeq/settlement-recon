from pathlib import Path
import pytest
from recon.contract import load

CONTRACTS = sorted((Path(__file__).parents[1] / "contracts").glob("*.yaml"))

def test_all_three_sources_have_contracts():
    assert {p.stem for p in CONTRACTS} == {"processor_settlement", "bank_statement", "ledger_payments"}

@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.stem)
def test_contract_is_valid(path):
    load(path)