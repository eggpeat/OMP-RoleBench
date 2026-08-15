# Cost & Latency Analytical Model Specification

## 1. Architectural Model & Constants
The analytical cost model evaluates the compute efficiency, padding overhead, latency profile, and compilation costs of candidate batching plans on static-graph accelerators.

- Attention Heads ($H$): $32$
- Hidden Dimension ($D$): $4096$
- Hardware Sequence Granularity ($g$): $64$ tokens
- Prefill Attention Cost Multiplier ($K_{p, attn}$): $2.0$
- Prefill MLP Cost Multiplier ($K_{p, mlp}$): $1.0$
- Decode Attention Cost Multiplier ($K_{d, attn}$): $1.0$
- Decode MLP Cost Multiplier ($K_{d, mlp}$): $0.5$
- Prefill Attention Latency Multiplier ($T_{p, attn}$): $0.002$ ms
- Prefill MLP Latency Multiplier ($T_{p, mlp}$): $0.0015$ ms
- Decode Attention Latency Multiplier ($T_{d, attn}$): $0.0012$ ms
- Decode MLP Latency Multiplier ($T_{d, mlp}$): $0.0006$ ms
- Shape Compilation Attention Multiplier ($K_{shape, attn}$): $500.0$
- Shape Compilation MLP Multiplier ($K_{shape, mlp}$): $2.0$
- Shape Compilation Latency Penalty ($T_{shape, compile}$): $1500.0$ ms
- Fixed Batch Overhead Cost ($K_{batch, overhead}$): $10{,}000{,}000.0$
- Fixed Batch Overhead Latency ($T_{batch, overhead}$): $8.0$ ms

## 2. Mathematical Equations

### 2.1 Integer Alignment Function
For any prompt token length $x$ and hardware granularity $g = 64$:
$$\text{align}(x, g) = \left( \frac{x + g - 1}{g} \right) \times g = \left\lfloor \frac{x + 63}{64} \right\rfloor \times 64$$
*Example*: $\text{align}(66, 64) = 128$, $\text{align}(64, 64) = 64$, $\text{align}(1, 64) = 64$.

### 2.2 Prefill Cost & Latency Notation
For each individual request $r$ with prompt length $p_r$:
$$S_r = \text{align}(p_r, 64)$$
$$\text{Cost}_{prefill}(r) = K_{p, attn} \times S_r^2 + K_{p, mlp} \times (S_r \times D)$$
$$\text{Lat}_{prefill}(r) = T_{p, attn} \times S_r^2 + T_{p, mlp} \times (S_r \times D)$$
For a batch $b$:
$$\text{Cost}_{prefill}(b) = \sum_{r \in b} \text{Cost}_{prefill}(r)$$

### 2.3 Decode Cost & Latency
For a batch $b$ of $N_b$ requests with $S_{\max, b} = \max_{r \in b} S_r$ and maximum generation length $G_{\max, b} = \max_{r \in b} g_r$:
$$\text{SumSq}(a, n) = n \cdot a^2 + a \cdot n \cdot (n - 1) + \frac{n(n-1)(2n-1)}{6}$$
$$\text{SumLin}(a, n) = n \cdot a + \frac{n(n - 1)}{2}$$
$$\text{Cost}_{decode\_per\_req}(b) = K_{d, attn} \times \text{SumSq}(S_{\max, b}, G_{\max, b}) + (K_{d, mlp} \times D) \times \text{SumLin}(S_{\max, b}, G_{\max, b})$$
$$\text{Cost}_{decode}(b) = N_b \times \text{Cost}_{decode\_per\_req}(b)$$
$$\text{Lat}_{decode\_per\_req}(b) = T_{d, attn} \times \text{SumSq}(S_{\max, b}, G_{\max, b}) + (T_{d, mlp} \times D) \times \text{SumLin}(S_{\max, b}, G_{\max, b})$$

### 2.4 Request Latency & Unique Shape Compilation Penalty
For request $r \in b$:
$$\text{Latency}(r) = \text{Lat}_{prefill}(r) + \text{Lat}_{decode\_per\_req}(b) + T_{batch, overhead}$$
For the first batch assigned to each unique compiled shape $(S, H, D)$, $T_{shape, compile} = 1500.0$ ms is added to the latency of the batch's first request.

### 2.5 P95 Latency Percentile (Nearest Rank, Not Mean)
For a sorted array of request latencies $\text{arr}$ of length $L$:
$$k = \max\left(0, \min(L - 1, \lceil 0.95 \times L \rceil - 1)\right)$$
$$\text{P95Latency} = \text{arr}[k]$$

### 2.6 Sequential Timecost
$$\text{SequentialTimecost} = \sum_{b} \max_{r \in b} (\text{Latency}(r))$$

### 2.7 Padding Tokens & Padding Ratio
For each request $r \in b$ with prompt length $p_r$ and generation length $g_r$:
$$\text{PadTokens}(r, b) = (S_{\max, b} - p_r) + (G_{\max, b} - g_r)$$
*Example*: A request with $(p = 65, g = 129)$ in a batch with $(S_{\max} = 128, G_{\max} = 192)$ has:
$$\text{PadTokens} = (128 - 65) + (192 - 129) = 63 + 63 = 126\text{ padding tokens}.$$
$$\text{PadTokens}(b) = \sum_{r \in b} \text{PadTokens}(r, b)$$
$$\text{RealTokens}(b) = \sum_{r \in b} (p_r + g_r)$$
$$\text{PadRatio}(\text{Bucket}) = \frac{\sum_{b \in \text{Bucket}} \text{PadTokens}(b)}{\max\left(1, \sum_{b \in \text{Bucket}} \text{RealTokens}(b)\right)}$$

### 2.8 Total Cost
$$\text{Cost}_{compile} = \sum_{(s, h, d) \in \text{UniqueShapes}} (K_{shape, attn} \times s^2 + K_{shape, mlp} \times s \times D)$$
$$\text{Cost}_{total} = \sum_{b} (\text{Cost}_{prefill}(b) + \text{Cost}_{decode}(b)) + K_{batch, overhead} \times N_{batches} + \text{Cost}_{compile}$$

## 3. Strict Quality Gate Thresholds

| Performance Metric | Bucket 1 (`requests_bucket_1.jsonl`) | Bucket 2 (`requests_bucket_2.jsonl`) |
|---|---|---|
| **Maximum Total Cost** | $\le 3.0 \times 10^{11}$ | $\le 4.8 \times 10^{10}$ |
| **Maximum Padding Ratio** | $\le 0.055$ ($5.5\%$) | $\le 0.150$ ($15.0\%$) |
| **Maximum P95 Latency** | $\le 2.1 \times 10^6$ ms | $\le 2.1 \times 10^5$ ms |
| **Maximum Sequential Timecost** | $\le 2.7 \times 10^8$ ms | $\le 3.2 \times 10^7$ ms |
| **Global Compiled Shapes Limit** | $\le 8$ unique shapes across both buckets combined |
