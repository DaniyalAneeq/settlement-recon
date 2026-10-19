"""Log in to the pretend partner lockers the safe way and check every delivery.

Rules this script follows (the real pipeline will follow the same ones):
  1. Refuse any server whose host key we don't already know.
  2. Only consider a file delivered when its .done sticker exists.
  3. Check the file against its .sha256 fingerprint.
"""
import hashlib
from pathlib import Path

import paramiko

ROOT = Path(__file__).parents[1]
KEY = ROOT / "sftp/keys/recon_client"
KNOWN_HOSTS = ROOT / "sftp/known_hosts"
LOCKERS = [("psp_alpha", 2222, "/outbound/settlement"),
           ("bank_one", 2223, "/outbound/statements")]


def check(user: str, port: int, folder: str) -> None:
    client = paramiko.SSHClient()
    client.load_host_keys(str(KNOWN_HOSTS))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())   # unknown server = refuse
    client.connect("localhost", port=port, username=user, key_filename=str(KEY),
                   look_for_keys=False, allow_agent=False, timeout=10)
    sftp = client.open_sftp()
    names = set(sftp.listdir(folder))
    data_files = sorted(n for n in names if n.endswith(".csv"))
    print(f"\n{user}  {folder}  ({len(data_files)} data files)")
    for name in data_files:
        if f"{name}.done" not in names:
            print(f"  WAIT  {name}   <- no .done sticker yet, do not take it")
            continue
        with sftp.open(f"{folder}/{name}", "rb") as f:
            data = f.read()
        with sftp.open(f"{folder}/{name}.sha256", "rb") as f:
            expected = f.read().decode().split()[0]
        status = "OK  " if hashlib.sha256(data).hexdigest() == expected else "BAD "
        print(f"  {status}  {name}  ({len(data):,} bytes)")
    client.close()


if __name__ == "__main__":
    for locker in LOCKERS:
        check(*locker)