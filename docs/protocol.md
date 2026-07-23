# Protocol Notes

## Finite Field

The initial implementation performs arithmetic over a prime field:

\[
\mathbb{F}_p
\]

A default prime will be selected for educational experiments.

## Shamir Secret Sharing

To share a secret \(s \in \mathbb{F}_p\), the dealer samples a random polynomial:

\[
Q(X) = s + a_1X + \cdots + a_tX^t
\]

The share distributed to party \(P_i\) is:

\[
(i, Q(i))
\]

Any \(t+1\) valid shares can reconstruct the secret by evaluating the interpolating polynomial at zero:

\[
s = Q(0)
\]

A set of at most \(t\) shares provides no information about the secret under the standard Shamir secret-sharing model.

## Local Addition

Given shares of \(a\) and \(b\), each party locally computes:

\[
Q_a(i) + Q_b(i)
\]

The resulting values form shares of:

\[
a+b
\]

No communication is required for this operation.

## Public Scalar Multiplication

For a public constant \(c\), each party locally computes:

\[
c \cdot Q_a(i)
\]

The resulting values form shares of:

\[
ca
\]

## Secure Multiplication

Secure multiplication will be introduced in a later milestone using Beaver multiplication triples.