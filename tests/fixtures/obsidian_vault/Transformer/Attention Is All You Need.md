---
title: "Attention 论文精读笔记"
authors: "Vaswani, Ashish; Shazeer, Noam; Parmar, Niki"
year: 2017
venue: "NeurIPS"
doi: "10.5555/3295222.3295349"
tags: [transformer, attention, deep-learning]
pdf: "attention.pdf"
---

# Attention 论文精读笔记

## 我的笔记

- Transformer 完全抛弃 RNN/CNN，只用 self-attention。
- 关键：multi-head attention + positional encoding。
- 值得复现的点：缩放点积注意力里的 1/sqrt(d_k)。

## 与我的工作

- 可以把 attention 的稀疏化思路迁移到我的时序预测项目。
