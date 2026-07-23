# Threat Model

## Scope

Mini MPC is an implementation of secure multi-party computation protocols.

The initial version simulates three parties and focuses on the correctness and privacy properties of Shamir secret sharing.

## Adversarial Model

The first implementation assumes semi-honest parties.

A semi-honest party:

- follows the protocol as specified;
- records all messages it receives;
- inspects its local inputs, randomness, and secret shares;
- attempts to infer information about the private inputs of other parties.

## Initial Assumptions

The first milestone assumes:

- three participating parties;
- private and authenticated communication channels are simulated;
- parties use cryptographically secure randomness;
- parties do not deviate from the protocol;
- only explicitly reconstructed outputs are revealed.

## Out of Scope

The initial implementation does not protect against:

- malicious parties sending malformed shares;
- parties aborting during the protocol;
- active network attackers;
- side-channel attacks;
- denial-of-service attacks;
- compromise of the operating system;
- insecure deletion of secrets from memory.

## Security Threshold

The precise corruption threshold depends on the secret-sharing parameters and the protocol being executed.

Each protocol implementation must document:

- the number of participating parties;
- the polynomial degree;
- the number of shares required for reconstruction;
- the maximum number of tolerated colluding parties.