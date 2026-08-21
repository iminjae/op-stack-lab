# Mintaray OP Stack — Architecture & Topology

## 개요

Mintaray는 Ethereum Sepolia를 L1 settlement layer로 사용하는 OP Stack 기반 L2 테스트 네트워크다.

하나의 서버에 모든 구성요소를 배치하지 않고,
두 개의 Raspberry Pi에 Sequencer 계층과 Verifier / Operations 계층을 분리하여 운영한다.

주요 목적은 다음과 같다.

- OP Stack L2의 실제 운영 구조 이해
- Sequencer / Verifier 역할 분리
- L1 derivation과 P2P unsafe propagation 관찰
- Batcher / Proposer / Challenger 운영
- Block Explorer 및 Observability 구성
- 실제 장애 대응과 운영 자동화 경험 확보

---

# 1. 전체 구조

~~~text
                         Internet
                            │
                      Cloudflare
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
 mintaray.xyz       explorer.mintaray.xyz   rpc.mintaray.xyz
    Portal               Blockscout           RPC Gateway
          │                 │                 │
          │                 │                 │
          ▼                 ▼                 ▼
      ┌─────────────────────────────────────────────┐
      │                 Private LAN                 │
      │                                             │
      │   Pi4                              Pi5      │
      │   Verifier / Ops                   Core     │
      │                                             │
      │   op-reth ◀──── OP Stack P2P ──── op-node │
      │      ▲              unsafe            │     │
      │      │                                ▼     │
      │   op-node                         op-reth   │
      │      │                                │     │
      │      │                                │     │
      │   challenger                     batcher   │
      │                                       │     │
      │                                  proposer  │
      │                                             │
      └──────────────────┬──────────────────────────┘
                         │
                         ▼
                 Ethereum Sepolia
                         │
                   L1 Settlement
~~~

---

# 2. Host 역할

## Pi5 — Sequencer / Core

Pi5는 L2 block production과 L1 submission을 담당하는 핵심 노드다.

구성:

~~~text
op-reth
op-node        Sequencer
op-batcher
op-proposer
RPC Gateway
~~~

역할:

### op-node

Sequencer mode로 실행된다.

- L2 block sequencing
- L1 derivation
- Engine API를 통한 op-reth 제어
- unsafe block P2P propagation

### op-reth

L2 Execution Client다.

담당:

- EVM execution
- transaction execution
- L2 state 관리
- JSON-RPC 제공
- Engine API 제공

### op-batcher

Sequencer가 생성한 L2 transaction data를 batch로 만들어
Ethereum Sepolia에 제출한다.

~~~text
L2 unsafe blocks
      ↓
op-batcher
      ↓
Ethereum Sepolia
~~~

### op-proposer

L2 output / dispute 관련 L1 submission을 담당한다.

---

# 3. Pi4 — Verifier / Operations

Pi4는 Sequencer와 독립적으로 L2 chain을 추적하고
Explorer 및 Observability 계층을 제공한다.

구성:

~~~text
op-reth
op-node        Verifier
op-challenger

Blockscout
Prometheus
Grafana
Loki
Alloy
Node Exporter
cAdvisor

Portal / Nginx
Cloudflared
~~~

### op-node

Verifier mode로 실행된다.

두 가지 경로로 L2 상태를 확보한다.

~~~text
1. P2P
Sequencer → Verifier
             ↓
           unsafe

2. L1 Derivation
Ethereum Sepolia
       ↓
    Verifier
       ↓
 safe / finalized
~~~

P2P를 통해 빠르게 받은 unsafe block은
이후 L1 derivation 결과를 통해 safe / finalized 상태로 검증된다.

### op-reth

Verifier 측 Execution Client다.

Blockscout는 이 Pi4 op-reth를 데이터 소스로 사용한다.

따라서 Public Explorer는 Sequencer RPC를 직접 바라보지 않고
Verifier가 확인한 chain state를 기반으로 동작한다.

### op-challenger

Fault dispute 관련 상태를 감시하고
OP Stack dispute game 구조를 검증하기 위해 운영한다.

---

# 4. Sequencer ↔ Verifier P2P

초기에는 두 op-node에서 P2P가 비활성화되어 있었다.

~~~text
--p2p.disable=true
~~~

이 상태에서 Pi4 Verifier는 최신 unsafe block을 직접 받을 수 없어
L1 derivation에만 의존했다.

결과적으로 신규 transaction이 Sequencer에서는 즉시 확인되었지만
Explorer에는 약 20~30분 뒤에 나타나는 문제가 발생했다.

현재는 Private LAN 기반 static P2P topology를 사용한다.

~~~text
Pi5 Sequencer
192.168.45.11:9222
       │
       │ static P2P
       ▼
Pi4 Verifier
~~~

구성 특성:

- TCP / UDP 9222
- Discovery disabled
- Static peer
- Persistent P2P identity
- Persistent peerstore
- Unsafe Block Signer 사용
- Private LAN에만 listener 노출

개선 후 측정:

~~~text
Pi4 Verifier RPC  = 4.581 seconds
Public Explorer   = 5.785 seconds
~~~

상세 내용:

~~~text
docs/incidents/001-explorer-visibility-delay.md
~~~

---

# 5. L2 상태 모델

Mintaray에서도 OP Stack의 unsafe / safe / finalized 상태를 구분해서 운영한다.

## Unsafe

~~~text
Sequencer
    ↓
P2P
    ↓
Verifier
~~~

가장 빠른 L2 head다.

아직 L1을 통해 완전히 검증되지 않은 상태다.

---

## Safe

~~~text
Sequencer
    ↓
Batcher
    ↓
Ethereum Sepolia
    ↓
Verifier Derivation
~~~

L1에 제출된 batch를 Verifier가 derivation한 상태다.

---

## Finalized

Ethereum L1 finality에 기반해 최종화된 L2 상태다.

~~~text
unsafe
   ↓
 safe
   ↓
finalized
~~~

P2P 추가는 unsafe propagation latency만 개선하며,
safe / finalized 검증 모델을 변경하지 않는다.

---

# 6. L1 Dependency

L1은 Ethereum Sepolia를 사용한다.

~~~text
L1 Chain ID
11155111

L2 Chain ID
7456133
~~~

op-node는 외부 L1 RPC Provider를 통해 다음 데이터를 조회한다.

- L1 headers
- receipts
- batch transactions
- deposits
- SystemConfig events
- derivation data

이 때문에 L1 RPC throughput 및 quota는
L2 derivation 성능에 직접적인 영향을 준다.

실제로 RPC rate limit으로 인해 derivation backlog가 발생한 장애가 있었다.

상세 내용:

~~~text
docs/incidents/002-l1-rpc-rate-limit.md
~~~

---

# 7. Public RPC

외부 사용자가 Pi5의 raw execution RPC에 직접 접근하지 않도록
별도의 RPC Gateway를 사용한다.

~~~text
User
 ↓
https://rpc.mintaray.xyz
 ↓
RPC Gateway
 ↓
Pi5 op-reth
~~~

Gateway에서 수행하는 주요 제한:

- JSON-RPC method allowlist
- request size 제한
- log query range 제한
- per-IP rate limit
- raw transaction rate limit
- debug / 위험 RPC 차단

Execution RPC 자체는 localhost에 제한하고
Gateway를 통해 필요한 method만 외부에 노출한다.

---

# 8. Block Explorer

Public Explorer:

~~~text
https://explorer.mintaray.xyz
~~~

구조:

~~~text
Pi4 op-reth
    ↓
Blockscout Backend
    ↓
Blockscout DB
    ↓
Mintaray Explorer Frontend
~~~

Blockscout가 Sequencer가 아닌 Verifier 측 chain state를 사용하도록 구성하여
독립적인 L2 verification path를 유지한다.

---

# 9. Observability

Pi4에서 중앙 Observability stack을 운영한다.

~~~text
Pi5 ─┐
     │
     ├── Prometheus ─── Grafana
     │
Pi4 ─┘

Docker logs
     ↓
   Alloy
     ↓
    Loki
     ↓
  Grafana
~~~

수집 대상에는 다음이 포함된다.

### Host

- CPU
- Memory
- Disk
- Temperature

### Container

- CPU
- Memory
- Network
- Container status

### OP Stack

- op-reth
- op-node
- op-batcher
- op-proposer
- op-challenger

Public Portal에서는 별도의 read-only Grafana dashboard를 통해
주요 infrastructure metrics를 확인할 수 있다.

운영 로그 및 상세 Explore 기능은 관리 용도로 분리한다.

---

# 10. Configuration Management

초기 구축 단계에서는 각 Raspberry Pi에 SSH 접속하여
Docker Compose configuration을 직접 관리했다.

현재 OP Stack node configuration은 Git을 source of truth로 사용하고
Ansible을 통해 배포한다.

~~~text
              Git
               │
               ▼
            Ansible
          ┌────┴────┐
          ▼         ▼
        Pi5         Pi4
     Sequencer    Verifier
~~~

Repository:

~~~text
nodes/
├── pi5/
│   └── opstack/
│       ├── compose.yml
│       └── compose.override.yaml
│
└── pi4/
    └── opstack/
        ├── compose.yml
        └── compose.override.yaml
~~~

Ansible은:

- Docker Compose config validation
- host별 configuration deployment
- 변경 시 configuration 적용
- one-host-at-a-time deployment
- service status 확인

을 수행한다.

현재 Configuration as Code 적용 범위는
**Pi4 / Pi5 OP Stack node configuration**이다.

Blockscout, Monitoring, Nginx, Cloudflare configuration은
추후 동일한 방식으로 중앙화할 예정이다.

---

# 11. Secret Management

Private key 및 credential은 Git repository에 저장하지 않는다.

다음 정보는 각 host에서 별도로 관리한다.

~~~text
JWT
Unsafe Block Signer key
Batcher key
Proposer key
Challenger credential
L1 RPC credentials
~~~

Git의 Compose configuration에는 실제 secret 대신
host-local secret file 경로만 존재한다.

예:

~~~text
/opt/opstack/secrets/op-node.env
/opt/opstack/secrets/op-batcher.env
~~~

원칙:

~~~text
Configuration → Git
Secrets       → Host local
Runtime State → Host / Volume
~~~

---

# 12. Runtime State

다음 데이터는 Git으로 관리하지 않는다.

- op-reth chain database
- PostgreSQL data
- Blockscout DB
- Prometheus TSDB
- Loki data
- Docker volumes

Git은 실행 방법과 configuration을 관리하고,
runtime state는 각 host에서 별도로 관리한다.

---

# 13. 장애 대응 문서

현재 실제 운영 과정에서 발생한 장애를 Incident Report와 Runbook으로 관리한다.

~~~text
docs/
├── incidents/
│   ├── 001-explorer-visibility-delay.md
│   └── 002-l1-rpc-rate-limit.md
│
└── runbooks/
    ├── p2p-health-check.md
    └── node-recovery.md
~~~

목표는 단순한 구축 결과가 아니라:

~~~text
Deploy
  ↓
Observe
  ↓
Diagnose
  ↓
Recover
  ↓
Document
  ↓
Automate
~~~

의 운영 사이클을 실제 환경에서 경험하고 기록하는 것이다.
