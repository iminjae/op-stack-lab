# Runbook — Safe chain 지연 진단 및 복구

## 목적

Mintaray에서 L1 scan 지연과 safe chain 지연을 분리하고,
배치 제출·L1 확인·derivation 결과를 함께 확인한다.
Pi5 Sequencer와 Pi4 Verifier를 각각 측정하며, 프로세스 생존만으로 복구를 판단하지 않는다.

아래 명령은 운영자가 대상 Pi의 Bash에서 사용할 읽기 전용 예시다.
이번 문서 작업에서는 운영 서버 접속이나 RPC 호출을 수행하지 않았다.
`curl`, `jq`, `docker compose`가 필요하며, 로그는 로컬에서 확인하고 credential 포함 URL 등은 공유 전에 제거한다.

---

# 1. 지표 정의

| 지표 | 계산식 (`optimism_syncStatus.result` 기준) | 단위 |
| --- | --- | --- |
| l1_scan_gap | `head_l1.number - current_l1.number` | L1 블록 |
| safe_origin_gap | `head_l1.number - safe_l2.l1origin.number` | L1 블록 |
| l2_safety_gap | `unsafe_l2.number - safe_l2.number` | L2 블록 |

scan gap은 L1 스캔 위치, safe origin gap은 safe L2가 참조하는 L1 origin의 지연이다.
L2 safety gap과 단위를 혼동하거나 블록 수를 측정 없이 시간으로 환산하지 않는다.
정상 여부는 confirmation 설정, 배치 주기, L1 상태와 시간에 따른 진행 추세를 함께 보고 판단한다.
Incident의 4 / 56을 공통 정상 임계값으로 사용하지 않는다.

# 2. Sync 지표 조회

Pi5 / Pi4의 op-node RPC는 각 호스트의 `127.0.0.1:8547`이다.
Public execution RPC나 Pi4 execution RPC `9545`와 혼동하지 않는다.

각 호스트에서 실행 시각과 다음 결과를 함께 기록하고, 일정 간격으로 다시 조회해 추세를 비교한다.

~~~bash
set -o pipefail
TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S %Z'
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"optimism_syncStatus","params":[],"id":1}' \
  http://127.0.0.1:8547 \
| jq -e '
  if .error != null then error("optimism_syncStatus JSON-RPC error")
  elif .result == null then error("missing sync result")
  else .result end
  | if ([.head_l1.number, .current_l1.number,
         .safe_l2.l1origin.number, .unsafe_l2.number,
         .safe_l2.number, .finalized_l2.number]
        | all(.[]; type == "number"))
    then {
      head_l1: .head_l1.number,
      current_l1: .current_l1.number,
      safe_l1_origin: .safe_l2.l1origin.number,
      unsafe_l2: .unsafe_l2.number,
      safe_l2: .safe_l2.number,
      finalized_l2: .finalized_l2.number,
      l1_scan_gap: (.head_l1.number - .current_l1.number),
      safe_origin_gap: (.head_l1.number - .safe_l2.l1origin.number),
      l2_safety_gap: (.unsafe_l2.number - .safe_l2.number)
    }
    else error("missing or nonnumeric sync fields") end'
~~~

HTTP 성공이어도 JSON-RPC `error`가 있으면 실패로 처리한다. 응답이 없거나 필수 필드가 누락되면
gap을 0으로 보완하지 않고 [Node Recovery](node-recovery.md) / [op-node 업데이트](op-node-upgrade.md)로 돌아간다.

# 3. 진단 분기

| 관측 | 다음 확인 |
| --- | --- |
| scan gap 증가와 429 / timeout 동반 | L1 RPC quota, rate limit, receipt 조회, 연결 상태. [Incident 002](../incidents/002-l1-rpc-rate-limit.md) 참고 |
| scan gap은 작지만 safe origin gap이 크고 감소하지 않음 | Batcher 제출, L1 확인, 배치 처리, sequencing window, reorg 순서로 확인 |
| Batcher는 running이지만 배치 확인이 없음 | 프로세스 생존과 제출 성공을 구분. 의존 RPC, 전송 오류, pending / replacement, 잔액·수수료·L1 포함 여부 확인 |
| L1 배치 확인은 있지만 safe가 진행하지 않음 | 해당 배치 처리·거부 로그, 대상 L2 범위, window, reorg, Pi5 / Pi4 각각의 derivation 확인 |
| Pi5는 진행하지만 Pi4 unsafe만 지연 | [P2P Health Check](p2p-health-check.md)와 Pi4 execution 상태 확인 |

여러 원인이 함께 존재할 수도 있다. scan gap이 작다는 사실은 모든 RPC 요청의 성공을 보장하지 않는다.

# 4. Batcher와 로그 확인

Pi5 Compose 서비스명은 `op-batcher`다. 저장소 선언은 v1.16.11이지만 실행 이미지도 확인한다.
Batcher는 `http://op-node:8547`의 rollup RPC에 의존한다.

~~~bash
cd /opt/opstack/compose
docker compose ps -a op-node op-batcher
batcher_id=$(docker compose ps -a -q op-batcher)
if [ -n "$batcher_id" ]; then
  docker inspect --format 'image={{.Config.Image}} status={{.State.Status}} restarts={{.RestartCount}}' "$batcher_id"
  docker inspect --format '{{json .Config.Env}}' "$batcher_id" \
  | jq -r '.[] | select(test("^OP_BATCHER_(WAIT_NODE_SYNC|BATCH_TYPE|MAX_CHANNEL_DURATION)="))'
else
  echo 'op-batcher container not found' >&2
fi
~~~

환경변수는 위 3개 항목만 표시한다. 값이 없으면 미확인으로 남기고 기본값을 추정해서 채우지 않는다.
CLI 인수에 의한 덮어쓰기가 없는지도 비밀을 출력하지 않고 대상 설정만 확인한다.

로컬에서 최근 로그를 읽는 예:

~~~bash
docker compose logs --since=40m --tail=300 --timestamps op-node
docker compose logs --since=40m --tail=300 --timestamps op-batcher
~~~

상한 300줄을 초과하는 구간은 이 출력만으로 모두 확인할 수 없다. 재발 여부를 판단할 때는 대상 시각 범위의 로그를
누락·rotation·출력 제한 없이 확인했는지 기록한다. Docker 시각이 UTC라면 KST와 대응시킨다.

확인 항목:

- `Dropping invalid span batch`, `sequence window expired`, `safe chain reorg`
- `payload is too old`, `failed to publish newly created block`
- Batcher의 의존 RPC 연결, 전송 실패, `Transaction confirmed`
- 실제 L1 배치 확인 시각·대상 범위와 이후 safe / finalized 진행

확인 로그만으로 derivation 성공이라고 판단하지 않는다. 실제 트랜잭션 정보를 확보할 수 있으면
L1 포함 결과와 대응하는 safe 진행을 대조한다. 누락된 해시를 만들어내지 않는다.

# 5. 조건부 복구와 설정 복원

먼저 위 읽기 전용 점검으로 장애 지점을 좁힌다. 복구 모드나 배치 타입 전환은 모든 동기화 장애에 적용하는 기본 처방이 아니다.

[Incident 004](../incidents/004-safe-chain-recovery.md)에서는 `admin_setRecoverMode(true)`와
`BATCH_TYPE=0` 임시 적용, 이후 `admin_setRecoverMode(false)`와 `BATCH_TYPE=1` 복원이 기록됐다.
이는 과거 적용 사실이며 그대로 재실행하는 지시가 아니다.

이번에 실행 버전별 관리 RPC의 의미·인수·상태 유지와 배치 설정 변경 적용 방법까지 검증하지 않았으므로,
상태 변경용 curl이나 재시작 명령은 싣지 않는다. 적용을 검토할 때는 다음을 먼저 확인한다.

1. **적용 조건**: L1 추적, execution, 의존 RPC의 기본 상태를 확인하고 배치 처리 / window / reorg 문제를 기록한다.
2. **버전별 확인**: 실행 중 op-node / Batcher의 공식 사양·소스에서 관리 RPC의 대상·동작과 `BATCH_TYPE`의 의미·호환성·반영 방법을 확인한다.
3. **영향과 복원 준비**: sequencing, 배치 형식·크기, L1 비용, safe 진행 영향을 평가한다. 변경 전 값, 복원 조건, 관측 시간, 중단 조건을 기록한다.
4. **제한 적용**: 대상을 Pi5의 해당 서비스로 한정하고 Pi4에 같은 관리 조작을 기계적으로 적용하지 않는다. 기존 localhost 관리 경계를 유지한다.
5. **변경 중 관측**: 시각과 함께 gap, L1 배치 확인, safe 진행, 관련 오류를 추적한다. 개선되지 않으면 반복 조작 대신 진단으로 돌아간다.
6. **설정 복원**: 복구 모드 해제와 일반 배치 설정 복원을 각각 확인하고 실행 상태와 설정 원본의 차이를 기록한다.
7. **복원 후 검증**: 아래 완료 조건을 복원 시각 이후의 새 관측으로 확인한다.

# 6. 복구 후 완료 조건

- 일반 설정 복원과 실행 상태를 확인했다.
- **복원 후** 새로운 L1 배치 확인과 safe 진행을 같은 관측 구간에서 확인했다.
- 여러 시점에서 safe / finalized 진행과 gap 추세를 확인했다. finality와 배치 주기를 고려한다.
- 해당 구간의 invalid batch, window 만료, reorg, payload / publish 경고 재발 여부를 확인했다.
- Pi4의 추적·관련 서비스도 독립적으로 확인했다.

미확인 항목이 있으면 완료로 처리하지 않는다. Incident 004의 13:52 결과는 13:57 일반 설정 복원보다 앞서므로,
복원 후 동작 검증에 사용할 수 없다.

## 관련 문서

- [Incident 002 — L1 RPC Rate Limit](../incidents/002-l1-rpc-rate-limit.md)
- [Incident 004 — Safe chain 진행 지연](../incidents/004-safe-chain-recovery.md)
- [op-node 버전·하드포크 대응](op-node-upgrade.md)
- [Grafana No Data 진단](monitoring-no-data.md)
