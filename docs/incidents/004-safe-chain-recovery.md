# Incident 004 — Safe chain 진행 지연 및 Batcher 복구

## 요약

2026-10-08 Mintaray에서 L1 scan gap이 작은 상태에서도 safe chain이 크게 뒤처졌다.
복구 모드 활성화와 Batcher `BATCH_TYPE=0` 임시 적용 후 상태를 관찰했으며,
safe origin 지연 감소와 복구 과정의 배치 확인 2건이 기록됐다.
복구 모드 해제 호출과 `BATCH_TYPE=1` 복원은 확인됐으나, 복원 이후 새 배치 확인과 safe 진행 지속은 미확인이다.

> 근거: 운영 점검 대화 요약 기준. 원본 로그 전체를 직접 확인한 기록이 아니다.
> 아래 모든 시각은 2026-10-08 Asia/Seoul, KST 기준이다.

---

## 발생일 및 대상 환경

- 점검일: 2026-10-08, 정확한 장애 시작 시각 미제공
- Mintaray 개인 테스트 / 포트폴리오용 OP Stack L2 (`7456133`), L1 Ethereum Sepolia (`11155111`)
- 주요 대응 대상: Pi5 Sequencer의 op-node / Batcher
- Pi4 Verifier를 포함한 전체 서비스의 최종 복구 상태는 이 자료만으로 확정하지 않는다.
- [Incident 003](003-op-node-l1-block-hash-mismatch.md) 이후 이어진 대응이지만 버전 변경과 본 증상의 인과관계는 미확인이다.

## 발견 경로와 주요 증상

`optimism_syncStatus` 점검에서 L1 scan과 safe origin의 지연이 서로 다르게 나타났다.
관측한 영향은 safe chain 진행 지연이다. 장애 지속 시간, 사용자 영향 규모, 피해액, 데이터 손실 여부는 단정하지 않는다.

로그 발췌:

~~~text
Dropping invalid span batch
sequence window expired
safe chain reorg
~~~

같은 날 `payload is too old`, `failed to publish newly created block` 경고도 관측됐다.
각각이 근본 원인인지 복구 과정의 증상인지는 확정되지 않았다.

## 타임라인과 실제 조치

| 시각 (KST) | 기록 | 검증 범위 |
| --- | --- | --- |
| 12:03:42 | `l1_scan_gap=4`, `safe_origin_gap=3545` | scan gap은 작고 safe origin 지연은 큼 |
| 정확한 시각 미제공 | `admin_setRecoverMode(true)` 호출 성공, Batcher `BATCH_TYPE=0` 임시 적용 | 조치 적용 확인. 위 측정과의 정확한 선후는 미제공 |
| 복구 과정 | safe 상태와 L1 배치 확인 결과 관찰 | 개별 조치의 효과를 분리한 실험은 아님 |
| 13:13:52 | `Transaction confirmed` 1건 | 복구 과정의 배치 확인 |
| 13:41경 | `admin_setRecoverMode(false)` 성공 응답 | 해제 호출 성공 확인 |
| 13:42:51 | `Transaction confirmed` 1건 | 복구 과정의 배치 확인 |
| 13:52:06 | 아래 sync 지표 확인 | `BATCH_TYPE=1` 복원 전 상태 |
| 13:52 점검 | 최근 40분 로그에서 `safe chain reorg` 없음, 위 배치 확인 2건 집계 | 해당 관측 구간에 한정 |
| 13:57경 | Batcher `BATCH_TYPE=1` 복원 상태 공유 | running, 재시작 횟수 0 및 아래 설정 확인 |

전체 배치 트랜잭션 해시는 제공되지 않았다. 확인 로그 2건을 임의의 트랜잭션 링크로 대체하지 않는다.

## 측정 결과

지표 정의와 단위는 [Safe chain Runbook](../runbooks/safe-chain-recovery.md)에 따른다.

| 지표 | 12:03:42 | 13:52:06 | 단위 |
| --- | ---: | ---: | --- |
| head_l1 | 미제공 | 11867887 | L1 블록 번호 |
| current_l1 | 미제공 | 11867883 | L1 블록 번호 |
| l1_scan_gap | 4 | 4 | L1 블록 |
| safe_origin_gap | 3545 | 56 | L1 블록 |
| unsafe_origin_gap | 미제공 | 5 | L1 블록 |
| safe_l2 | 미제공 | 2204970 | L2 블록 번호 |
| unsafe_l2 | 미제공 | 2205273 | L2 블록 번호 |
| finalized_l2 | 미제공 | 2204100 | L2 블록 번호 |
| l2_safety_gap | 미제공 | 303 | L2 블록 |

`unsafe_origin_gap = head_l1.number - unsafe_l2.l1origin.number`이다.
`11867887 - 11867883 = 4`, `2205273 - 2204970 = 303`이며,
4나 56은 이번 관측값이지 모든 상황에 적용할 정상 임계값이 아니다.

13:57경 공유된 Batcher 상태:

~~~text
status = running
restart count = 0
OP_BATCHER_WAIT_NODE_SYNC=true
OP_BATCHER_BATCH_TYPE=1
OP_BATCHER_MAX_CHANNEL_DURATION=150
~~~

이는 해당 시점의 프로세스와 설정 확인이며 배치 제출 성공의 증거와는 구분한다.

## 진단 과정과 원인 판단

[Incident 002](002-l1-rpc-rate-limit.md)는 RPC 제한으로 `current_l1` 자체가 뒤처져 scan gap이 커졌던 사례다.
이번에는 scan gap이 4인 상태에서도 safe origin gap이 3545로 관측됐다.
따라서 L1 head 추적만 보고 정상화로 판단하지 않고 배치 제출 → L1 확인 → 배치 처리 → safe 진행을 나눠 확인했다.

| 근거 수준 | 판단 |
| --- | --- |
| 관측 사실 | invalid span batch, sequencing window 만료, safe chain reorg 관련 로그와 safe 지연이 함께 기록됐다. |
| 진단 방향 | 배치 제출·확인·처리와 sequencing window / reorg 상태를 함께 점검할 필요가 있다. |
| 미확정 | span batch 구현 버그, 특정 RPC provider, 이전 버전 업데이트를 근본 원인으로 지목할 근거는 부족하다. |
| 효과 해석 | 복구 모드·배치 타입 변경 적용과 지연 감소가 기록됐으나, 특정 조치 하나가 전체 문제를 해결했다고 단정하지 않는다. |

## 복구 결과 및 검증 범위

- 관측됨: safe origin gap이 3545에서 56으로 감소했고 복구 과정의 배치 확인 2건이 기록됐다.
- 확인됨: 13:41경 복구 모드 해제 호출 성공, 이후 13:57경 `BATCH_TYPE=1` 복원 상태 공유.
- 제한된 관측: 13:52 점검의 최근 40분 동안 `safe chain reorg`가 없었다.
- 미확인: `BATCH_TYPE=1` 복원 이후 새 배치 확인, safe 진행 지속, 관련 오류 재발 여부.
- 미확인: Pi4를 포함한 전체 서비스의 최종 복구.

13:52 상태와 13:13:52 / 13:42:51 배치 확인은 모두 13:57 설정 복원보다 앞선 결과다.
이 자료를 일반 설정 복원 후 정상 동작 검증 완료 또는 재발 없는 완전 해결로 해석하지 않는다.

## 미확인 사항과 후속 작업

- 일반 설정 복원 시각 이후의 새 배치 확인과 safe / finalized 진행을 여러 시점에서 기록한다.
- 같은 구간의 invalid span batch, window 만료, reorg, payload / publish 경고 재발을 확인한다.
- Pi4의 실행 상태, unsafe 수렴, safe / finalized 진행과 관련 서비스 상태를 독립적으로 확인한다.
- 원본 로그, 변경 시각, 배치 트랜잭션의 실제 식별 정보가 확보되면 원인 분석을 보강한다.

## 재발 방지 과제

다음은 아직 적용 여부가 확인되지 않은 제안이다.

- scan gap, safe origin gap, L2 safety gap을 별도로 추적한다.
- Batcher running 상태와 L1 배치 확인, safe 진행을 함께 감시한다.
- 임시 설정 적용 시 복원 조건·시각과 복원 이후 검증 구간을 남긴다.
- sequencing window와 배치 처리 오류를 포함한 진단 기록을 유지한다.

## 관련 Runbook 및 출처

- [Safe chain 지연 진단 및 복구](../runbooks/safe-chain-recovery.md)
- [op-node 버전·하드포크 대응](../runbooks/op-node-upgrade.md)
- [Node Recovery](../runbooks/node-recovery.md)
- [Incident 003 — op-node 기동 중단](003-op-node-l1-block-hash-mismatch.md)
- [Incident 002 — L1 RPC Rate Limit](002-l1-rpc-rate-limit.md)
- 운영 점검 대화 요약: 2026-10-08, 원본 로그 전체 및 전체 트랜잭션 해시 미첨부
