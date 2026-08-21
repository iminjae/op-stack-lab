#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import re
import shutil
import sys
import tomllib
from datetime import datetime


ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

CHAIN_FIELDS = [
    "baseFeeVaultRecipient",
    "l1FeeVaultRecipient",
    "sequencerFeeVaultRecipient",
    "operatorFeeVaultRecipient",
]

ROLE_FIELDS = [
    "l1ProxyAdminOwner",
    "l2ProxyAdminOwner",
    "systemConfigOwner",
    "unsafeBlockSigner",
    "batcher",
    "proposer",
    "challenger",
]

PRESERVED_GLOBAL_FIELDS = [
    "opcmAddress",
    "l1ContractsLocator",
    "l2ContractsLocator",
]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_toml(path: pathlib.Path) -> tuple[str, dict]:
    if not path.is_file():
        fail(f"파일을 찾을 수 없음: {path}")

    text = path.read_text(encoding="utf-8")

    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        fail(f"TOML 파싱 실패: {path}: {exc}")

    return text, parsed


def get_single_chain(document: dict, label: str) -> dict:
    chains = document.get("chains")

    if not isinstance(chains, list) or len(chains) != 1:
        fail(f"{label} intent의 [[chains]]가 정확히 1개가 아님")

    return chains[0]


def validate_address(field: str, value: object) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.fullmatch(value):
        fail(f"{field}가 유효한 EVM 주소가 아님: {value!r}")

    if value.lower() == ZERO_ADDRESS:
        fail(f"{field}가 zero address임")

    return value


if len(sys.argv) != 4:
    fail(
        "사용법: migrate-intent-addresses.py "
        "<old-intent.toml> <new-intent.toml> <expected-chain-id>"
    )

old_path = pathlib.Path(sys.argv[1]).expanduser().resolve()
new_path = pathlib.Path(sys.argv[2]).expanduser().resolve()

try:
    expected_chain_id = int(sys.argv[3], 10)
except ValueError:
    fail(f"chain ID가 숫자가 아님: {sys.argv[3]}")

old_text, old_doc = read_toml(old_path)
new_text, new_doc_before = read_toml(new_path)

old_chain = get_single_chain(old_doc, "old")
new_chain_before = get_single_chain(new_doc_before, "new")

new_chain_id_raw = new_chain_before.get("id")

if not isinstance(new_chain_id_raw, str):
    fail(f"새 intent의 chain id 형식이 잘못됨: {new_chain_id_raw!r}")

try:
    new_chain_id = int(new_chain_id_raw, 0)
except ValueError:
    fail(f"새 intent chain id를 숫자로 변환할 수 없음: {new_chain_id_raw}")

if new_chain_id != expected_chain_id:
    fail(
        f"새 intent chain ID 불일치: "
        f"expected={expected_chain_id}, actual={new_chain_id}"
    )

preserved_before = {
    field: new_doc_before.get(field)
    for field in PRESERVED_GLOBAL_FIELDS
}

old_roles = old_chain.get("roles")
new_roles = new_chain_before.get("roles")

if not isinstance(old_roles, dict):
    fail("기존 intent에서 [chains.roles]를 찾을 수 없음")

if not isinstance(new_roles, dict):
    fail("새 intent에서 [chains.roles]를 찾을 수 없음")

values: dict[str, str] = {}

for field in CHAIN_FIELDS:
    value = old_chain.get(field)

    # 이전 intent에 operator fee vault 필드가 없었던 경우,
    # 기존 base fee vault recipient를 동일하게 사용한다.
    if field == "operatorFeeVaultRecipient" and value is None:
        value = old_chain.get("baseFeeVaultRecipient")
        print(
            "NOTICE: 기존 operatorFeeVaultRecipient가 없어 "
            "baseFeeVaultRecipient를 동일하게 사용"
        )

    values[field] = validate_address(field, value)

for field in ROLE_FIELDS:
    value = old_roles.get(field)

    # 이전 intent에 L2 owner가 없었던 경우에만 L1 owner와 동일하게 사용.
    if field == "l2ProxyAdminOwner" and value is None:
        value = old_roles.get("l1ProxyAdminOwner")
        print(
            "NOTICE: 기존 l2ProxyAdminOwner가 없어 "
            "l1ProxyAdminOwner를 동일하게 사용"
        )

    values[field] = validate_address(field, value)

patched_text = new_text

for field, value in values.items():
    pattern = re.compile(
        rf'(?m)^(\s*{re.escape(field)}\s*=\s*)"[^"]*"(\s*(?:#.*)?)$'
    )

    patched_text, replacement_count = pattern.subn(
        lambda match: f'{match.group(1)}"{value}"{match.group(2)}',
        patched_text,
    )

    if replacement_count != 1:
        fail(
            f"새 intent에서 {field} 치환 횟수가 1이 아님: "
            f"{replacement_count}"
        )

# 파일을 쓰기 전에 원본 백업
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup_path = new_path.with_name(
    f"{new_path.name}.before-address-migration-{timestamp}"
)

shutil.copy2(new_path, backup_path)
new_path.write_text(patched_text, encoding="utf-8")

# 결과 재검증
_, new_doc_after = read_toml(new_path)
new_chain_after = get_single_chain(new_doc_after, "patched")

after_chain_id = int(new_chain_after["id"], 0)

if after_chain_id != expected_chain_id:
    fail("주소 이식 과정에서 chain ID가 변경됨")

for field in PRESERVED_GLOBAL_FIELDS:
    if new_doc_after.get(field) != preserved_before[field]:
        fail(f"주소 이식 과정에서 {field}가 변경됨")

for field in CHAIN_FIELDS:
    if new_chain_after.get(field, "").lower() != values[field].lower():
        fail(f"{field} 이식 검증 실패")

after_roles = new_chain_after.get("roles", {})

for field in ROLE_FIELDS:
    if after_roles.get(field, "").lower() != values[field].lower():
        fail(f"{field} 이식 검증 실패")

print()
print("ADDRESS MIGRATION OK")
print(f"backup              = {backup_path}")
print(f"chain_id_decimal    = {after_chain_id}")
print(f"chain_id_hex        = {new_chain_after['id']}")
print(f"opcmAddress         = {new_doc_after.get('opcmAddress')}")
print(f"l1ContractsLocator  = {new_doc_after.get('l1ContractsLocator')}")
print(f"l2ContractsLocator  = {new_doc_after.get('l2ContractsLocator')}")

print()
print("[fee vault recipients]")
for field in CHAIN_FIELDS:
    print(f"{field:30} = {values[field]}")

print()
print("[chain roles]")
for field in ROLE_FIELDS:
    print(f"{field:30} = {values[field]}")
