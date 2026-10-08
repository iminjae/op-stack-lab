# Mintaray OP Stack Lab

Ethereum Sepolia를 L1 settlement layer로 사용하는 **OP Stack 기반 L2 네트워크 운영 프로젝트**입니다.

단순 배포에 그치지 않고 Sequencer / Verifier 분리 운영, L1 derivation, P2P propagation, Block Explorer, Observability, 장애 대응 및 Configuration as Code까지 실제 환경에서 구성하고 검증하는 것을 목표로 합니다.

---

## Live Services

| Service | URL |
| --- | --- |
| Network Portal | https://mintaray.xyz |
| Block Explorer | https://explorer.mintaray.xyz |
| Monitoring | https://monitor.mintaray.xyz/public-dashboards/ddbb2de850b84d789dd01d40fc873b1a?from=now-3h&to=now&timezone=browser |
| Public RPC | https://rpc.mintaray.xyz |

### Network

| Item | Value |
| --- | --- |
| L1 | Ethereum Sepolia |
| L1 Chain ID | `11155111` |
| L2 Chain ID | `7456133` |
| Stack | OP Stack |

> 본 네트워크는 개인 인프라 환경에서 운영하는 테스트 / 포트폴리오용 L2 네트워크입니다.

---

# Architecture

Mintaray는 두 개의 독립된 호스트에 Sequencer와 Verifier / Operations 역할을 분리하여 운영합니다.

~~~text
                         Ethereum Sepolia
                                ▲
                                │
                    Batch / Proposal / Derivation
                                │
             ┌──────────────────┴──────────────────┐
             │                                     │
             │                                     │
      Pi5 — Sequencer                       Pi4 — Verifier
             │                                     │
         op-node                              op-node
         op-reth                              op-reth
         batcher                              challenger
         proposer                                  │
             │                                     │
             └──────── OP Stack P2P ──────────────▶│
                         unsafe blocks              │
                                                   ▼
                                               Blockscout
                                                   │
                                                   ▼
                                                Explorer
~~~

상세 구조:

[Architecture & Topology](docs/architecture/topology.md)

---

# Node Roles

## Pi5 — Sequencer / Core

- `op-node` — Sequencer
- `op-reth` — L2 Execution Client
- `op-batcher` — L2 data → Ethereum Sepolia
- `op-proposer`
- Public RPC Gateway
- OP Stack P2P listener

## Pi4 — Verifier / Operations

- `op-node` — Verifier
- `op-reth`
- `op-challenger`
- Blockscout
- Prometheus
- Grafana
- Loki
- Alloy
- Node Exporter
- cAdvisor
- Portal / Reverse Proxy

---

# Unsafe / Safe / Finalized

Mintaray에서는 OP Stack의 L2 상태를 분리해서 관찰합니다.

~~~text
unsafe
Sequencer
   │
   └──── P2P ────▶ Verifier


safe
Sequencer
   │
   └──── Batch ───▶ Ethereum Sepolia
                         │
                         └──── Derivation ───▶ Verifier


finalized
Ethereum L1 finality
        │
        └────▶ Verifier
~~~

P2P는 low-latency `unsafe` block propagation을 담당하며,
`safe` 및 `finalized` 상태는 기존 L1 derivation 기반 검증 모델을 유지합니다.

---

# Explorer Visibility Improvement

초기에는 Sequencer와 Verifier 사이의 OP Stack P2P가 비활성화되어 있었습니다.

그 결과 Blockscout가 사용하는 Verifier가 신규 block을 L1 derivation 이후에만 확인할 수 있었고,
신규 transaction이 Explorer에 표시되기까지 약 **20~30분**의 지연이 발생했습니다.

Private LAN 기반 static P2P topology를 구성한 이후 실제 transaction을 기준으로 측정한 결과:

| Stage | Visibility |
| --- | ---: |
| Pi4 Verifier RPC | **4.581 s** |
| Public Blockscout Explorer | **5.785 s** |

Explorer 반영 지연을 수십 분에서 수초 수준으로 개선했습니다.

상세 장애 분석:

[Incident 001 — Explorer Transaction Visibility Delay](docs/incidents/001-explorer-visibility-delay.md)

---

# L1 RPC / Derivation Incident

OP Stack `op-node`가 Ethereum Sepolia 데이터를 derivation하는 과정에서
외부 L1 RPC Provider의 rate limit / daily quota에 도달하면서 L1 backlog가 발생했습니다.

관찰한 주요 증상:

- `429 Too Many Requests`
- Daily quota exhaustion
- `context deadline exceeded`
- `head_l1 - current_l1` 증가
- Safe chain progression 지연

`optimism_syncStatus`를 통해 L1 derivation backlog를 수치화하고
RPC concurrency / rate limit / batch size 조정 및 충분한 quota 확보를 통해 catch-up을 완료했습니다.

정상화 후:

~~~text
head_l1 - current_l1 ≈ 4
~~~

수준으로 복구했습니다.

상세 장애 분석:

[Incident 002 — L1 RPC Rate Limit](docs/incidents/002-l1-rpc-rate-limit.md)

위 scan gap은 당시 복구 관측값이며 공통 정상 기준이 아닙니다.
scan gap이 작아도 safe chain이 뒤처질 수 있으므로 [Safe chain 진단 Runbook](docs/runbooks/safe-chain-recovery.md)에서
safe origin gap과 L2 safety gap을 함께 확인합니다.

---

# Observability

Pi4에 중앙 Observability stack을 구성하여 Pi4 / Pi5와 OP Stack 서비스를 함께 관측합니다.

~~~text
Pi4 ─┐
     │
     ├──▶ Prometheus ───▶ Grafana
     │
Pi5 ─┘


Docker logs
     │
     ▼
   Alloy
     │
     ▼
    Loki
     │
     ▼
  Grafana
~~~

관측 대상:

- CPU
- Memory
- Disk
- Temperature
- Docker container resources
- `op-node`
- `op-reth`
- `op-batcher`
- `op-proposer`
- `op-challenger`

Public Portal에서는 read-only Monitoring dashboard를 제공합니다.

데이터가 보이지 않을 때는 [Grafana No Data 진단](docs/runbooks/monitoring-no-data.md)에서
패널·데이터소스·scrape·프로세스 상태를 구분합니다.

---

# Public RPC Gateway

Raw execution RPC를 인터넷에 직접 노출하지 않고 별도의 RPC Gateway를 사용합니다.

~~~text
External Client
      │
      ▼
rpc.mintaray.xyz
      │
      ▼
RPC Gateway
      │
      ▼
Pi5 op-reth
~~~

적용 항목:

- JSON-RPC method allowlist
- Request size 제한
- Log query range 제한
- Per-IP rate limit
- Raw transaction rate limit
- Debug / 위험 RPC 차단

---

# Configuration as Code

초기 구축 단계에서는 Raspberry Pi에 SSH 접속하여 Docker Compose 설정을 직접 관리했습니다.

현재 Pi4 / Pi5의 **OP Stack node configuration은 Git을 source of truth로 사용하고 Ansible을 통해 배포**합니다.

~~~text
                  Git
                   │
                   ▼
                Ansible
              ┌────┴────┐
              ▼         ▼
             Pi5       Pi4
          Sequencer   Verifier
~~~

현재 Git에서 관리하는 구성:

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

Ansible deployment:

- Docker Compose configuration validation
- Host별 configuration 배포
- 변경된 경우에만 Compose 적용
- One-host-at-a-time deployment
- Service status 확인

현재 Configuration as Code 적용 범위는 **OP Stack node configuration**입니다.

Blockscout / Monitoring / Nginx / Cloudflare 구성은 추후 동일한 방식으로 중앙화할 예정입니다.

---

# Secret Management

실제 credential은 Git repository에 저장하지 않습니다.

Host-local로 관리되는 항목:

- JWT
- Unsafe Block Signer private key
- Batcher private key
- Proposer private key
- Challenger credential
- L1 RPC credentials

Git에는 실제 값 대신 secret 파일의 reference만 존재합니다.

~~~text
Configuration  → Git
Secrets        → Host local
Runtime State  → Host / Docker Volume
~~~

---

# Operations Documentation

실제 운영 과정에서 발생한 문제와 복구 절차를 문서화합니다.

## Incident Reports

- [Explorer Transaction Visibility Delay](docs/incidents/001-explorer-visibility-delay.md)
- [L1 RPC Rate Limit / Derivation Lag](docs/incidents/002-l1-rpc-rate-limit.md)
- [L1 블록 해시 검증 실패로 인한 op-node 기동 중단 (2026-10-07)](docs/incidents/003-op-node-l1-block-hash-mismatch.md)
- [Safe chain 진행 지연 및 Batcher 복구 (2026-10-08)](docs/incidents/004-safe-chain-recovery.md)

10월 기록은 운영 점검 대화 요약 기준입니다. 일반 설정 복원 이후의 배치·safe 진행과
Pi4를 포함한 최종 복구는 후속 확인 항목으로 남아 있습니다.

## Runbooks

- [P2P Health Check](docs/runbooks/p2p-health-check.md)
- [Node Recovery](docs/runbooks/node-recovery.md)
- [op-node 버전·하드포크 대응](docs/runbooks/op-node-upgrade.md)
- [Safe chain 지연 진단 및 복구](docs/runbooks/safe-chain-recovery.md)
- [Grafana No Data 진단](docs/runbooks/monitoring-no-data.md)

---

# Repository Structure

~~~text
op-stack-lab/
├── ansible/
│   ├── inventory.ini
│   └── playbooks/
│       └── deploy-opstack.yml
│
├── deployer/
│   └── .deployer-live/
│       ├── genesis.json
│       ├── intent.toml
│       ├── rollup.json
│       └── state.json
│
├── docs/
│   ├── architecture/
│   ├── incidents/
│   └── runbooks/
│
├── nodes/
│   ├── pi4/
│   │   └── opstack/
│   └── pi5/
│       └── opstack/
│
├── scripts/
│   └── migrations/
│
└── README.md
~~~

---

# Operational Principles

이 프로젝트에서는 다음 원칙으로 장애를 대응합니다.

~~~text
Observe
   ↓
Measure
   ↓
Isolate
   ↓
Recover
   ↓
Validate
   ↓
Document
   ↓
Automate
~~~

특히 장애 발생 시 다음 작업을 바로 수행하지 않습니다.

- Chain DB 삭제
- `docker compose down -v`
- Genesis 재생성
- Rollup config 재생성
- Node 전체 rebuild

먼저 execution / derivation / P2P / RPC / indexing 계층을 분리해서 원인을 확인합니다.

---

# Current Status

### Completed

- OP Stack L2 deployment
- Sequencer / Verifier host separation
- Batcher / Proposer / Challenger operation
- Ethereum Sepolia settlement
- Private Sequencer ↔ Verifier P2P
- Public RPC Gateway
- Blockscout Explorer
- Prometheus / Grafana observability
- Loki / Alloy log aggregation
- Public Monitoring dashboard
- Git-based OP Stack node configuration
- Ansible deployment
- Incident Reports / Runbooks

### In Progress / Planned

- Faucet final E2E verification
- Blockscout configuration centralization
- Monitoring configuration centralization
- Nginx / Cloudflare configuration centralization
- Ansible automation expansion
- Withdrawal prove / finalize flow
- Bridge UI completion

---

# Related Services

- Portal: https://mintaray.xyz
- Explorer: https://explorer.mintaray.xyz
- Monitoring: https://monitor.mintaray.xyz/public-dashboards/ddbb2de850b84d789dd01d40fc873b1a
- RPC: https://rpc.mintaray.xyz

