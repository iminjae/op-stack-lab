# Incident 003 — L1 블록 해시 검증 실패로 인한 op-node 기동 중단

## 요약

2026-10-07 Grafana No Data를 점검하던 중 Pi4 / Pi5 op-node metrics target의 연결 실패와
op-node 초기화 오류를 확인했다. 당시 두 호스트의 op-node 이미지는 v1.19.3이었다.
2026-10-08 Pi5에서는 v1.19.8 실행과 `optimism_syncStatus` 응답이 확인됐다.
Pi4의 업데이트 후 최종 상태와 전체 서비스 복구는 이 자료만으로 확정하지 않는다.

> 근거: 운영 점검 대화 요약 기준. 원본 로그 전체를 직접 확인한 기록이 아니다.
> 날짜와 시각은 Asia/Seoul, KST 기준이며 정확한 장애 시작·종료 시각은 제공되지 않았다.

---

## 발생일 및 대상 환경

- 발견일: 2026-10-07
- 네트워크: Mintaray 개인 테스트 / 포트폴리오용 OP Stack L2
- L1: Ethereum Sepolia (`11155111`), L2 Chain ID: `7456133`
- Pi5: Sequencer, op-reth, op-node, Batcher, Proposer
- Pi4: Verifier, op-reth, op-node 및 운영 서비스

## 발견 경로와 관측한 영향

Grafana에서 데이터가 보이지 않아 Prometheus targets를 비교했다.

| 대상 | 관측 상태 |
| --- | --- |
| node-exporter, cAdvisor, reth | target 정상 |
| Pi4 / Pi5 op-node | target down, `connect: connection refused` |
| Pi5 batcher / proposer | target down, `connect: connection refused` |

모든 target이 실패한 상황은 아니었다. op-node 초기화 실패와 Batcher의 op-node 접근 실패가
함께 관측되어, 패널 문제뿐 아니라 프로세스와 의존 서비스까지 점검했다.
사용자 영향 규모, 피해액, 데이터 손실 여부, 장애 지속 시간은 측정 자료가 없다.

## 주요 증상

op-node 오류 발췌:

~~~text
Error initializing the rollup node
failed to verify block hash: computed ... but RPC said ...
~~~

Batcher 오류 발췌:

~~~text
address unavailable (http://op-node:8547)
~~~

Proposer는 target down이 확인됐으나 동일한 애플리케이션 종료 로그는 제공되지 않았다.
Batcher에서 확인한 오류를 Proposer에도 발생한 것으로 확대하지 않는다.

## 타임라인

| 시점 (KST) | 관측 / 대응 |
| --- | --- |
| 2026-10-07, 상세 시각 미제공 | Grafana No Data → Prometheus target별 상태 비교 |
| 2026-10-07, 상세 시각 미제공 | op-node 초기화·블록 해시 검증 오류와 Batcher 의존성 오류 확인 |
| 2026-10-07 점검 기록 | Pi4 / Pi5 op-node v1.19.3 확인, 클라이언트와 L1 호환성 검토 |
| 2026-10-08 Pi5 출력 | op-node:v1.19.8 실행 및 `optimism_syncStatus` 응답 확인 |
| 2026-10-08 후속 점검 | [Incident 004](004-safe-chain-recovery.md)의 safe chain 지연 대응으로 이어짐 |

업데이트 실행 명령과 정확한 적용 시각은 제공되지 않았다.
두 사건은 연속된 점검 기록이지만 버전 업데이트가 invalid span batch를 유발했다는 인과관계는 확인되지 않았다.

## 진단 과정과 원인 판단

1. 정상 target과 실패 target을 비교해 모니터링 전체 장애인지 개별 서비스 장애인지 구분했다.
2. op-node 초기화 실패와 Batcher가 의존하는 `op-node:8547` 접근 실패를 확인했다.
3. 실행 버전과 Sepolia 호환 요구 조건을 대조했다.
4. Pi5의 변경된 이미지와 RPC 응답을 확인하고, 호스트별 후속 검증을 분리했다.

2026-10-08 문서 작성 시 [공식 op-node v1.19.8 릴리스 노트](https://github.com/ethereum-optimism/optimism/releases/tag/op-node/v1.19.8)를 확인했다.
공식 안내는 Sepolia OP Stack 노드를 10월 6일 Glamsterdam 하드포크 전에 v1.19.5 이상으로 업데이트하도록 요구한다.

| 근거 수준 | 판단 |
| --- | --- |
| 확인된 사실 | 당시 v1.19.3은 공식 최소 요구 버전에 미달했다. 초기화 중 블록 해시 검증이 실패했다. |
| 추정 | L1 하드포크와 클라이언트 호환성 불일치가 관련됐을 가능성이 있어 우선 점검 대상이다. |
| 미입증 | 해당 버전 미달이 이 해시 불일치의 세부 원인이라는 결론은 원본 블록·응답·로그 대조 없이 확정할 수 없다. |

## 실제 조치와 복구 검증 범위

- 버전 변경 후 상태에 해당하는 Pi5 op-node:v1.19.8 실행 출력과 RPC 응답이 확인됐다.
- 정확한 변경 명령, 배포 순서, 적용 시각은 재구성하지 않는다.
- RPC 응답만으로 safe / finalized 진행, 배치 제출, Proposer 및 Pi4 복구까지 완료됐다고 판단하지 않는다.
- 이후 safe chain 지연과 복구 관측은 [Incident 004](004-safe-chain-recovery.md)에 별도로 기록한다.

## 미확인 사항과 후속 작업

- Pi4 실행 이미지, 초기화 성공, RPC 및 head 진행, metrics 수집을 별도 확인한다.
- Pi5 Batcher / Proposer의 연결 회복과 실제 역할 수행을 확인한다.
- 당시 블록 번호·해시, RPC 응답, 초기화 로그를 확보할 수 있으면 세부 원인을 대조한다.
- 저장소의 Pi4 / Pi5 Compose는 문서 작성 시 v1.19.3을 선언하고 있다. Pi5 운영 기록의 v1.19.8과 차이가 있으므로 양쪽 호스트의 실제 설정과 대조하고 별도 설정 변경으로 반영한다. 이번 작업에서는 Compose / Ansible을 변경하지 않았다.

## 재발 방지 과제

다음은 적용 완료 사항이 아닌 후속 제안이다.

- L1 / L2 하드포크 일정과 클라이언트 최소 요구 버전을 함께 관리한다.
- 호스트별 변경 전후 이미지·상태·검증 시각을 남긴다.
- target down 감시와 RPC / head 진행 검증을 함께 운영한다.
- 서버 긴급 변경 후 저장소 설정 원본과의 차이를 해소한다.

## 관련 Runbook 및 출처

- [op-node 버전·하드포크 대응](../runbooks/op-node-upgrade.md)
- [Grafana No Data 진단](../runbooks/monitoring-no-data.md)
- [Safe chain 지연 진단 및 복구](../runbooks/safe-chain-recovery.md)
- [Node Recovery](../runbooks/node-recovery.md)
- 운영 점검 대화 요약: 2026-10-07 ~ 2026-10-08, 원본 로그 전체 미첨부
- [공식 op-node v1.19.8 릴리스 노트](https://github.com/ethereum-optimism/optimism/releases/tag/op-node/v1.19.8)
