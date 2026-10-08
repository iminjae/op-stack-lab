# Runbook — OP Stack Node Recovery

## 목적

Mintaray OP Stack의 Pi5 Sequencer 또는 Pi4 Verifier가 재부팅, Docker 장애,
프로세스 비정상 종료 등의 이유로 정상 동작하지 않을 때 안전하게 상태를 확인하고 복구하기 위한 절차다.

이 Runbook의 기본 원칙은 다음과 같다.

- DB를 먼저 삭제하지 않는다.
- Genesis / Rollup config를 다시 만들지 않는다.
- 전체 Docker Compose를 무조건 내리지 않는다.
- 문제가 발생한 계층부터 좁혀서 확인한다.
- Execution Client 상태를 먼저 확인한 후 op-node를 점검한다.
- 복구 후 unsafe / safe / finalized 진행 상태를 확인한다.

## 증상별 진입점

| 증상 | 상세 절차 |
| --- | --- |
| op-node 초기화 실패, L1 블록 해시 검증 오류, 하드포크 대응 | [op-node 버전·하드포크 대응](op-node-upgrade.md) |
| safe 정체, L1 scan과 safe origin 지연의 차이, Batcher 제출 문제 | [Safe chain 지연 진단 및 복구](safe-chain-recovery.md) |
| Grafana No Data, metrics target down, 로그 패널 공백 | [Grafana No Data 진단](monitoring-no-data.md) |
| Pi4 unsafe가 Pi5를 따라가지 못함 | [P2P Health Check](p2p-health-check.md) |

초기화 오류는 단순 재시작 전에 버전과 L1 / L2 호환성을 확인한다.
최근 사건은 [Incident 003](../incidents/003-op-node-l1-block-hash-mismatch.md)과
[Incident 004](../incidents/004-safe-chain-recovery.md)를 참고한다.

---

# 1. 현재 노드 구성

## Pi5 — Sequencer

~~~text
op-reth
op-node        Sequencer
op-batcher
op-proposer
~~~

주요 RPC:

~~~text
L2 JSON-RPC     127.0.0.1:8545
op-node RPC     127.0.0.1:8547

op-reth metrics 192.168.45.11:9001
op-node metrics 192.168.45.11:7300
batcher metrics 192.168.45.11:7301
proposer metrics 192.168.45.11:7302

P2P             192.168.45.11:9222 TCP/UDP
~~~

---

## Pi4 — Verifier / Operations

~~~text
op-reth
op-node        Verifier
op-challenger
~~~

주요 RPC:

~~~text
L2 JSON-RPC       127.0.0.1:9545
op-node RPC       127.0.0.1:8547

op-reth metrics   192.168.45.10:9001
op-node metrics   192.168.45.10:7300
challenger metrics 192.168.45.10:7303
~~~

---

# 2. 기본 복구 순서

노드 문제가 발생하면 다음 순서로 확인한다.

~~~text
Host
 ↓
Docker
 ↓
op-reth
 ↓
op-node
 ↓
P2P
 ↓
Batcher / Proposer / Challenger
 ↓
Sync Status
 ↓
Explorer / Monitoring
~~~

DB 삭제나 chain rebuild는 이 절차의 마지막에도 자동으로 수행하지 않는다.

---

# 3. Host 상태 확인

대상 Pi에 SSH 접속 후 실행한다.

~~~bash
date
uptime
free -h
df -h /
~~~

확인 항목:

- 시스템이 정상 부팅되었는가
- 메모리 부족이 없는가
- root filesystem이 가득 차지 않았는가
- 비정상적인 load가 지속되는가

---

# 4. Docker 상태 확인

~~~bash
systemctl is-active docker
docker info >/dev/null && echo "DOCKER=OK"
~~~

정상:

~~~text
active
DOCKER=OK
~~~

Docker가 비활성 상태라면:

~~~bash
sudo systemctl start docker
~~~

그 후 다시 확인한다.

---

# 5. OP Stack 전체 상태 확인

Pi4 / Pi5 공통:

~~~bash
cd /opt/opstack/compose

docker compose ps
~~~

모든 서비스가 `Up` 상태인지 확인한다.

Pi5 정상 서비스:

~~~text
op-reth
op-node
op-batcher
op-proposer
~~~

Pi4 정상 서비스:

~~~text
op-reth
op-node
op-challenger
~~~

현재 서비스들은 `restart: unless-stopped` 정책으로 운영하므로
정상적인 host reboot 이후 Docker가 올라오면 자동으로 다시 실행되는 것이 정상이다.

---

# 6. Host Reboot 후 확인

예정된 재부팅이라면 별도로 `docker stop`을 먼저 실행할 필요는 없다.

~~~bash
sudo reboot
~~~

Host가 다시 올라온 후:

~~~bash
cd /opt/opstack/compose

docker compose ps
~~~

서비스가 자동 복구되었는지 확인한다.

재부팅 직후에는 op-node derivation state가 잠시 재정렬되면서
unsafe / safe 값이 즉시 최신 상태가 아닐 수 있다.

몇 분간 진행 방향을 확인한 뒤 장애 여부를 판단한다.

---

# 7. Execution Client 확인

op-node보다 먼저 op-reth를 확인한다.

## Pi5

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"eth_blockNumber",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8545 \
| jq
~~~

## Pi4

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"eth_blockNumber",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:9545 \
| jq
~~~

정상이라면 block number가 반환된다.

RPC 연결 자체가 실패하면:

~~~bash
cd /opt/opstack/compose

docker compose ps op-reth

docker compose logs \
  --since=10m \
  op-reth \
| tail -150
~~~

부터 확인한다.

---

# 8. op-node Sync Status 확인

Pi4 / Pi5 공통:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"optimism_syncStatus",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    unsafe_l2: .result.unsafe_l2.number,
    safe_l2: .result.safe_l2.number,
    finalized_l2: .result.finalized_l2.number,
    current_l1: .result.current_l1.number,
    head_l1: .result.head_l1.number
  }'
~~~

중점적으로 확인할 항목:

~~~text
head_l1 - current_l1
Pi5 unsafe_l2
Pi4 unsafe_l2
safe_l2 진행 여부
finalized_l2 진행 여부
~~~

정상 steady-state에서는 `current_l1`이 L1 head 근처를 지속적으로 따라간다.

safe origin과 L2 safety gap, timeout 및 JSON-RPC 오류 검사까지 포함한 조회는
[Safe chain Runbook](safe-chain-recovery.md)의 명령을 사용한다.

---

# 9. Pi5 Sequencer 상태 확인

Pi5:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"admin_sequencerActive",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq
~~~

정상:

~~~text
result = true
~~~

Sequencer가 active인데 block이 진행하지 않으면 op-node 및 op-reth 로그를 함께 확인한다.

~~~bash
cd /opt/opstack/compose

docker compose logs \
  --since=10m \
  op-node \
| grep -Ei \
'sequencer|engine|payload|reset|error|failed|warn' \
| tail -150
~~~

---

# 10. P2P 상태 확인

Pi4 Verifier가 최신 unsafe block을 받기 위해서는 Pi5와의 P2P 연결이 정상이어야 한다.

Pi4:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"opp2p_peerStats",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq
~~~

정상 기준:

~~~text
connected >= 1
blocksTopic >= 1
~~~

더 자세한 절차는:

~~~text
docs/runbooks/p2p-health-check.md
~~~

를 참고한다.

---

# 11. 특정 서비스만 복구

서비스 하나에만 문제가 있는 경우 전체 Compose를 내리지 않는다.

## op-node만 재시작

~~~bash
cd /opt/opstack/compose

docker compose restart op-node
~~~

그 후:

~~~bash
docker compose ps op-node
~~~

및:

~~~text
optimism_syncStatus
~~~

를 확인한다.

---

## op-reth만 재시작

~~~bash
cd /opt/opstack/compose

docker compose restart op-reth
~~~

Execution RPC가 다시 응답하는지 먼저 확인한 후 op-node 상태를 확인한다.

필요한 경우:

~~~bash
docker compose restart op-node
~~~

를 수행한다.

---

## Batcher

Pi5:

~~~bash
cd /opt/opstack/compose

docker compose restart op-batcher
docker compose ps op-batcher
~~~

Batcher 문제는 unsafe block 생성 자체와 동일한 문제는 아니다.

Sequencer가 정상적으로 unsafe block을 생성하더라도
Batcher 장애가 지속되면 safe chain 진행이 지연될 수 있다.

---

## Proposer

Pi5:

~~~bash
cd /opt/opstack/compose

docker compose restart op-proposer
docker compose ps op-proposer
~~~

---

## Challenger

Pi4:

~~~bash
cd /opt/opstack/compose

docker compose restart op-challenger
docker compose ps op-challenger
~~~

---

# 12. Compose 설정 변경 후 복구

현재 Pi4 / Pi5 OP Stack configuration은 Git + Ansible을 source of truth로 관리한다.

따라서 정상적인 configuration 변경은 서버에서 직접 수정하지 않는다.

Mac:

~~~text
Git configuration
      ↓
Ansible
      ↓
Pi4 / Pi5
~~~

Pi5만 적용:

~~~bash
cd ~/Desktop/op-stack-lab/ansible

ansible-playbook \
  playbooks/deploy-opstack.yml \
  --limit pi5
~~~

Pi4만 적용:

~~~bash
ansible-playbook \
  playbooks/deploy-opstack.yml \
  --limit pi4
~~~

둘 다:

~~~bash
ansible-playbook \
  playbooks/deploy-opstack.yml
~~~

긴급하게 서버에서 직접 수정했다면
장애가 해결된 후 반드시 Git configuration에도 동일한 변경을 반영하여
configuration drift를 제거한다.

---

# 13. 로그 확인 순서

## op-reth

~~~bash
docker compose logs \
  --since=10m \
  op-reth \
| tail -200
~~~

## op-node

~~~bash
docker compose logs \
  --since=10m \
  op-node \
| tail -200
~~~

## Pi5 Batcher

~~~bash
docker compose logs \
  --since=10m \
  op-batcher \
| tail -200
~~~

필요할 때만 특정 오류를 필터링한다.

~~~bash
docker compose logs \
  --since=10m \
  op-node \
| grep -Ei \
'429|timeout|engine|payload|reset|p2p|error|failed|warn'
~~~

---

# 14. L1 Derivation Lag 확인

다음 상태라면 P2P 또는 execution DB 문제라고 바로 판단하지 않는다.

~~~text
unsafe 정상 진행
safe 진행 정체
current_l1 지연
~~~

이 경우 L1 RPC 상태를 확인한다.

대표적인 오류:

~~~text
429 Too Many Requests
daily request limit reached
context deadline exceeded
~~~

관련 Incident: [L1 RPC Rate Limit](../incidents/002-l1-rpc-rate-limit.md).

`head_l1.number - current_l1.number`가 작더라도 safe chain은 뒤처질 수 있다.
이 경우 RPC quota 문제로 바로 판단하지 않고 safe origin gap, 배치 제출·L1 확인·배치 처리,
sequencing window와 reorg를 [Safe chain Runbook](safe-chain-recovery.md)에 따라 점검한다.

---

# 15. Pi5와 Pi4 Unsafe Head 비교

Pi5와 Pi4에서 각각:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"optimism_syncStatus",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    unsafe: .result.unsafe_l2.number,
    safe: .result.safe_l2.number,
    finalized: .result.finalized_l2.number
  }'
~~~

P2P가 정상적으로 동작하는 steady-state에서는:

~~~text
Pi5 unsafe ≈ Pi4 unsafe
~~~

여야 한다.

Pi4 unsafe가 장시간 수백 블록 이상 뒤처지면 P2P Runbook을 수행한다.

---

# 16. Pi4 복구 후 Explorer 확인

Pi4의 block head가 정상으로 복구되었으면
Blockscout가 이를 따라오는지도 확인한다.

먼저 Pi4 RPC가 최신 block을 반환하는지 확인한다.

그 후:

~~~text
https://explorer.mintaray.xyz
~~~

에서 최신 block 및 transaction이 나타나는지 확인한다.

Pi4 RPC에는 transaction이 있지만 Explorer에는 없는 경우
OP Stack node recovery 문제가 아니라 Blockscout indexing 문제로 분리한다.

---

# 17. 두 Host가 동시에 장애 난 경우

둘 다 내려간 경우 다음 순서를 권장한다.

~~~text
1. Pi5 부팅
2. Docker 확인
3. op-reth 확인
4. op-node Sequencer 확인
5. Batcher / Proposer 확인
6. Pi5 block 생성 확인

7. Pi4 부팅
8. Docker 확인
9. op-reth 확인
10. op-node Verifier 확인
11. P2P 연결 확인
12. Pi4 unsafe head convergence 확인
13. Challenger 확인
14. Explorer 확인
~~~

Sequencer 측을 먼저 정상화한 뒤 Verifier가 이를 따라가도록 하는 방식이다.

---

# 18. 절대 바로 수행하지 않는 작업

다음 작업은 장애 초기에 수행하지 않는다.

~~~text
rm -rf op-reth database
docker compose down -v
genesis.json 재생성
rollup.json 재생성
새 chain deployment
node database 전체 초기화
RPC provider 무작정 반복 교체
~~~

이 작업들은 정상 데이터를 제거하거나
새로운 synchronization 비용을 만들 수 있다.

---

# 19. Secret 주의

다음 명령은 가급적 사용하지 않는다.

~~~text
docker inspect <container>
docker compose config
~~~

전체 environment를 그대로 출력하면 secret이 노출될 수 있다.

특히 다음 정보는 외부에 공유하지 않는다.

- private key
- JWT 값
- RPC API key
- provider credential
- `.env` 전체 내용

필요한 environment 항목은 key를 제한하여 확인한다.

---

# 20. 복구 완료 기준

다음 조건을 모두 만족하면 OP Stack node recovery를 완료한 것으로 판단한다.

## Pi5

- op-reth Up
- op-node Up
- Sequencer active = true
- unsafe head 진행
- Batcher Up
- Proposer Up
- P2P host 정상
- 일반 설정 복원 후 새 L1 배치 확인과 safe 진행을 함께 확인
- 복원 후 관련 오류 재발 여부 확인 ([상세 기준](safe-chain-recovery.md))

## Pi4

- op-reth Up
- op-node Up
- P2P peer connected
- Pi4 unsafe ≈ Pi5 unsafe
- safe / finalized 진행
- Challenger Up

## User-facing

- Public RPC 정상
- Explorer 최신 block 반영
- 신규 transaction 조회 가능
