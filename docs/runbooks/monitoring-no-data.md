# Runbook — Grafana No Data 진단

## 목적

Mintaray의 Grafana No Data를 패널, 데이터소스, Prometheus scrape, metrics listener,
프로세스 초기화 계층으로 나눠 확인한다. 로그 패널은 Alloy → Loki 경로를 별도로 확인한다.

Pi4가 중앙 Observability를 담당한다. 저장소에는 OP Stack metrics 포트 선언이 있지만
Prometheus / Grafana / Loki / Alloy의 배포·접속 설정 전체는 없다.
해당 관리 주소, 데이터소스 UID, job label은 실제 운영 설정에서 확인하며 포트를 추정하지 않는다.
아래 명령은 운영자가 사용할 읽기 전용 예시이며 이번 문서 작업에서 실행하지 않았다.

---

# 1. 패널 시간 범위와 쿼리

- 패널 종류가 metrics인지 로그인지 먼저 확인한다.
- 시간 범위와 마지막 데이터 시각, 대시보드 timezone을 확인한다. Incident 시각은 Asia/Seoul, KST다.
- 변수의 host / instance / job 선택, label 변경, 쿼리 필터와 집계 구간을 확인한다.
- 권한이 있는 관리 화면에서 Query Inspector / Explore로 같은 데이터소스와 쿼리를 확인한다.
- 로그가 발생하지 않은 구간은 빈 결과일 수 있다. 로그 패널의 빈 결과를 metrics scrape 실패로 해석하지 않는다.

# 2. 데이터소스 연결

Grafana 데이터소스의 연결 점검 결과와 쿼리 오류를 확인한다.
Prometheus 직접 조회는 되는데 Grafana만 실패한다면 데이터소스 URL, 접근 권한, proxy 및 패널 쿼리를 확인한다.
여러 패널이 동시에 비어도 곧바로 롤업 전체 장애라고 판단하지 않는다.
공개 대시보드에 운영 로그나 관리 접근 권한을 추가하지 않는다.

# 3. Prometheus target과 scrape 오류

운영 중인 Prometheus 관리 화면의 Targets에서 대상별 상태, Last scrape, Last error와 실제 endpoint를 확인한다.
쿼리 화면에서는 다음을 조회하고 실제 label로 호스트 / 서비스를 좁힌다.

~~~promql
up
~~~

- `up=1`: 해당 target의 scrape 성공. 롤업 전체 정상 동작을 보장하지 않는다.
- `up=0`: scrape 실패. Last error와 해당 프로세스를 점검한다.
- 시계열 자체가 없음: target 발견·설정·label·조회 시간 범위도 확인한다. 0과 동일하게 취급하지 않는다.

| scrape 오류 | 점검 방향 |
| --- | --- |
| `connect: connection refused` | listener 부재, 잘못된 포트 / 바인딩, 프로세스 종료·초기화 실패, 명시적 연결 거부 |
| timeout | 네트워크 경로, 방화벽, 과부하, scrape timeout |
| HTTP / 파싱 오류 | metrics 경로·응답 형식, endpoint 종류, scrape 설정 |

[Incident 003](../incidents/003-op-node-l1-block-hash-mismatch.md)에서는 node-exporter / cAdvisor / reth가 정상이었고
Pi4 / Pi5 op-node와 Pi5 batcher / proposer target은 connection refused였다.
이처럼 정상·실패 target을 비교해 공통 경로와 개별 서비스 문제를 구분한다.

# 4. Metrics listener와 접근 경로

저장소 Compose / override에서 확인한 endpoint:

| 서비스 | Pi5 | Pi4 |
| --- | --- | --- |
| op-reth | `192.168.45.11:9001` | `192.168.45.10:9001` |
| op-node | `192.168.45.11:7300` | `192.168.45.10:7300` |
| op-batcher | `192.168.45.11:7301` | 해당 없음 |
| op-proposer | `192.168.45.11:7302` | 해당 없음 |
| op-challenger | 해당 없음 | `192.168.45.10:7303` |

각 endpoint의 `/metrics`를 확인한다. 예를 들어 Pi4에서 Pi5 op-node 경로를 확인할 때:

~~~bash
curl --fail --silent --show-error --connect-timeout 3 --max-time 10 \
  --output /dev/null --write-out 'HTTP %{http_code}\n' \
  http://192.168.45.11:7300/metrics
~~~

이 요청 성공은 HTTP 접근 확인이다. Prometheus가 해당 응답을 정상 scrape했는지는 Targets에서 별도 확인한다.
호스트에서 성공하더라도 Prometheus 컨테이너의 경로는 다를 수 있으므로 실제 scraper의 접근 경로와 endpoint도 대조한다.

대상 Pi에서 listener 및 매핑을 읽기 전용으로 확인한다.

~~~bash
ss -lnt | grep -E ':(7300|7301|7302|7303|9001)([[:space:]]|$)'
cd /opt/opstack/compose
docker compose port op-node 7300
docker compose ps -a op-node
~~~

Docker의 포트 전달 방식에 따라 host `ss`에 listener가 직접 보이지 않을 수 있다.
`ss` 결과만으로 중단을 단정하지 않고 Compose 매핑, 실제 접속, 컨테이너 상태를 함께 확인한다.
metrics는 기존 LAN 접근 범위에서 점검하며 외부 공개를 복구 방법으로 사용하지 않는다.

# 5. 프로세스 상태와 초기화 로그

대상 Pi에서 서비스 상태와 최근 초기화 로그를 로컬로 확인한다.

~~~bash
cd /opt/opstack/compose
docker compose ps -a
docker compose logs --since=10m --tail=200 --timestamps op-node
~~~

Pi5에서는 `op-batcher`, `op-proposer` 로그도 각각 확인한다.
공유 전에 credential 포함 RPC URL, private key, JWT 등 민감 정보를 제거한다.
로그 출력 제한이나 rotation으로 필요한 구간이 빠졌으면 미확인으로 남긴다.

- op-node 초기화·블록 해시 검증 실패: [버전·하드포크 Runbook](op-node-upgrade.md)
- Batcher `address unavailable (http://op-node:8547)`: 의존하는 op-node RPC 상태 확인
- Proposer target down: Proposer 자체 로그 확인. Batcher 오류를 Proposer에도 있었다고 대입하지 않는다.
- 컨테이너가 running이어도 초기화 성공·RPC·head 진행을 별도로 확인한다.

# 6. 로그 패널만 비어 있는 경우

metrics가 수집되고 로그만 없으면 Docker 로그 발생 여부 → Alloy 수집 → Loki ingest / query → Grafana 순서로 확인한다.
실제 서비스명, label, 시간 범위, 권한, 보존 기간을 운영 설정에서 대조한다.
이 저장소만으로 Loki / Alloy 관리 포트나 점검 명령을 확정하지 않는다.
로그가 없는 것과 로그 수집이 실패한 것을 구분해 기록한다.

# 7. 수정 후 검증

1. 대상 target의 새 scrape 시각과 `up=1`, scrape 오류 해소를 확인한다.
2. 패널의 새 샘플과 쿼리 결과를 확인한다. 이전 데이터가 보이는 것만으로 완료하지 않는다.
3. op-node RPC와 head 진행을 [Safe chain Runbook](safe-chain-recovery.md)으로 확인한다.
4. Pi5 Batcher의 L1 배치 확인과 safe 진행, Proposer 연결 및 역할 수행을 확인한다.
5. Pi4 상태도 독립적으로 확인하고 로그 수집 문제였다면 새 로그의 전달을 확인한다.

모니터링 수집 복구와 롤업 동작 복구의 결과를 각각 기록한다.

## 관련 문서

- [Incident 003 — op-node 기동 중단](../incidents/003-op-node-l1-block-hash-mismatch.md)
- [Node Recovery](node-recovery.md)
- [Architecture & Topology](../architecture/topology.md)
