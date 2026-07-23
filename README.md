# Mini MPC

An MPC framework implementing Shamir secret sharing, secure arithmetic, and privacy-preserving joint statistics.

## Goals

The project is designed to explore the connection between cryptographic theory and protocol implementation:

- finite-field arithmetic;
- polynomial evaluation and interpolation;
- Shamir secret sharing;
- local computation over secret shares;
- secure addition and public scalar multiplication;
- secure multiplication using Beaver triples;
- communication between independent parties;
- privacy-preserving joint statistics;
- protocol correctness, security assumptions, and performance.

## Planned Milestones

### Milestone 1: Secret-Sharing Core

- finite-field arithmetic;
- polynomial evaluation;
- Shamir secret generation and reconstruction;
- secure sum in a single-process simulation;
- unit tests.

### Milestone 2: Isolated Parties

- one process per party;
- message passing between parties;
- private local state;
- protocol transcripts and communication metrics.

### Milestone 3: Secure Multiplication

- Beaver multiplication triples;
- offline and online phases;
- secure dot product;
- multiplication correctness tests.

### Milestone 4: Privacy-Preserving Analytics

- secure sum;
- secure mean;
- secure variance;
- secure dot product;
- fixed-point encoding.

### Milestone 5: Evaluation

- runtime benchmarks;
- communication-volume measurements;
- network-latency experiments;
- comparison with an established MPC framework.

## Project Structure

```text
mini-mpc/
├── docs/
│   ├── protocol.md
│   └── threat_model.md
├── examples/
│   └── secure_sum.py
├── src/
│   └── mini_mpc/
│       ├── field.py
│       ├── polynomial.py
│       ├── protocols.py
│       ├── shamir.py
│       └── share.py
├── tests/
├── pyproject.toml
└── README.md