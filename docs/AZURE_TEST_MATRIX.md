# Matrice de confrontation Azure — tous les cas appels / events / spans

Objectif : confronter chaque garantie du tracker à du **trafic Azure réel**. Chaque cas
précise comment le déclencher, la forme d'événement attendue, et le critère de réussite
dérivé (jamais un chiffre stocké). Statuts : ✅ fixture RÉELLE déjà capturée · 🔲 à capturer.

**Prérequis (déploiements Azure OpenAI)**
- `gpt-4o` ou `gpt-4.1` (chat + cache automatique de prompt ≥ 1024 tokens)
- un modèle o-series (`o4-mini`…) pour `reasoning_tokens`
- `text-embedding-3-small` (ou large)
- facultatif : `gpt-4o-audio` (cas audio), un déploiement Cohere/AI Foundry (multi-provider)

**Critères transverses (valables pour TOUS les cas)**
- `event_contributing_tokens == provider_total_tokens` quand tout est vérifié+connu (sinon la raison est un cas listé)
- `event_total_mismatch == 0` sauf cas conçu pour l'écart
- aucun champ dérivé dans le JSONL (relire le fichier brut)
- chaque drapeau observé appartient au producteur attendu

---

## A. Grain quantité — additivité & précision

| # | Cas | Déclenchement Azure | Événement attendu | Confronte | Statut |
|---|---|---|---|---|---|
| A1 | Appel simple | chat 1 tour, court | input+output EXACT total_contributing ; somme == total | INV-2/4, réconciliation | ✅ (stress) |
| A2 | Cache hit | 2 appels, même préfixe ≥ 1024 tokens, < 5 min | 2ᵉ appel : `cached_input` subtotal_of input > 0 ; contributing == provider_total (cache compte 0) | INV-4 no-double-count | ✅ call1/call2 |
| A3 | Cache MISS puis HIT | A2 en vérifiant call1 cached=0, call2 cached>0 | delta de latence + cached_tokens ; mêmes totaux contributifs | axe overlap | ✅ |
| A4 | Reasoning | o-series, question de raisonnement | `reasoning` subtotal_of output > 0 ; contributing == provider_total | INV-3 pureté (PAS de type "estimated") | 🔲 |
| A5 | Cache + reasoning combinés | o-series + préfixe ≥ 1024 ×2 | les DEUX subtotals présents ; somme reste == provider_total | le test Phase 5 en réel | 🔲 |
| A6 | Embeddings | embeddings sur un texte | `EMBEDDING` EXACT total_contributing ; total == prompt_tokens | surface embeddings | 🔲 |
| A7 | Image en entrée | chat avec image (vision) | input inclut les tokens image ; si détail par modalité absent → PAS de quantité inventée | INV-6 (jamais inventer) | 🔲 |
| A8 | Audio (si déployé) | gpt-4o-audio | `audio_input`/`audio_output` subtotals ; somme == provider_total | table additivité audio | 🔲 |
| A9 | max_tokens tronqué | `max_tokens: 5` | finish_reason=length ; usage EXACT quand même ; aucun drapeau qualité | exactitude ≠ complétude de réponse | 🔲 |

## B. Streaming — le cœur de la supersession

| # | Cas | Déclenchement Azure | Événement attendu | Confronte | Statut |
|---|---|---|---|---|---|
| B1 | Stream complété avec usage | `stream: true` + `stream_options.include_usage` | 1 événement, usage EXACT source=provider_stream_final | provenance | 🔲 |
| B2 | Stream sans include_usage | `stream: true` sans option | sortie ESTIMATE (tokenizer partiel) ou unknown selon config ; flag partial_stream_estimate | INV-6 | 🔲 |
| B3 | Stream interrompu (client) | couper la connexion après N chunks | output ESTIMATE + flags stream_interrupted/partial_stream_estimate ; total = borne inférieure | INV-5/6 | 🔲 |
| B4 | Interrompu PUIS final | B3 puis récupérer l'usage final (même request_correlation_id) | partiel superseded=true par le final ; trace somme le FINAL seul | supersession corrélée | 🔲 |
| B5 | Timeout de stream | timeout client court | output quantity=None precision=unknown reason=stream_timeout ; compté à part | unknown ≠ 0 | 🔲 |
| B6 | Stream + usage keep-alive Azure | longs streams (chunks de ping) | le consommateur SSE ne fabrique rien depuis les keep-alives | robustesse parse | 🔲 |

## C. Échecs & autorité opérationnelle

| # | Cas | Déclenchement Azure | Événement attendu | Confronte | Statut |
|---|---|---|---|---|---|
| C1 | Content filter Azure | prompt déclenchant le filtre | ÉVÉNEMENT CONSERVÉ, authoritative=false → contributing 0 ; l'usage réel (si renvoyé) reste visible pour l'audit | INV-7 — spécialité Azure | ✅ filter_block |
| C2 | 401 (clé invalide) | clé fausse | événement échec, raw_usage_missing, non-autoritaire, http_status=401 | capture des échecs | 🔲 |
| C3 | 429 (rate limit) | dépasser le TPM du déploiement | échec 429 + retry_count ; l'échec contribue 0 | observation facts | 🔲 |
| C4 | Retry après 429 → succès | même span, 2 tentatives | 2 événements, MÊME span_id, request_correlation_id DIFFÉRENTS ; trace = usage du succès seul | pourquoi la corrélation ≠ span | 🔲 |
| C5 | Livraison en double | rejouer le même event_id au collector | 1 seule ligne persistée (append_unique) | idempotence | 🔲 (scénario E simulé existe) |
| C6 | Réponse 200 sans usage | (rare — mock si introuvable en réel) | raw_usage_missing, contributing 0, PAS de zéro inventé | INV-6 | 🔲 |
| C7 | api-version exotique | vieille api-version | champs inconnus → ignorés proprement, rien d'inventé, version-drift signalé | robustesse schéma | 🔲 |

## D. Topologie spans / traces — propagation

| # | Cas | Déclenchement | Attendu | Confronte | Statut |
|---|---|---|---|---|---|
| D1 | 1 trace, 1 span, 1 appel | baseline | rollup == l'événement | grains | ✅ implicite |
| D2 | Pipeline RAG séquentiel | span embedding → span génération (contexte injecté) | 2 événements, spans frères/imbriqués corrects ; total trace = somme des 2 | grain trace | 🔲 |
| D3 | N appels async parallèles | asyncio.gather sur 5+ appels | chaque événement sur SON span ; aucun croisement business_id/workflow | Phase 1 en réel | 🔲 |
| D4 | N appels via ThreadPool | `ContextPropagatingExecutor` sur 5+ appels réels | idem D3 sous threads | le P0 d'hier en réel | 🔲 |
| D5 | Retry dans un span | C4 vu côté topologie | 1 span, 2 correlation ids | modèle tentative | 🔲 |
| D6 | Agent + outil + sous-appel | agent → tool_call → 2ᵉ appel LLM | parent_span_id chaîné ; attribution par sous-arbre correcte | analytics agent | 🔲 |
| D7 | Cross-service (2 process) | service A appelle service B avec les en-têtes X-TokenTracker-* | même trace_id des deux côtés ; parent résolu | headers | 🔲 |
| D8 | Propagation cassée | corrompre un en-tête côté B | racine fraîche + flag propagation_lost (jamais silencieux) | fail-visible | 🔲 |
| D9 | Trace multi-provider | Azure OpenAI + Azure Cohere dans une trace | totaux par provider + trace cohérents ; additivités respectives | table par provider | 🔲 |

## E. Double comptabilité — SDK vs proxy (la confrontation reine)

Le MÊME appel mesuré par les deux chemins indépendants doit donner le MÊME résultat.

| # | Cas | Déclenchement | Attendu | Statut |
|---|---|---|---|---|
| E1 | Appel chat via SDK+capture ET via proxy | pointer base_url du SDK sur le proxy (provider azure_openai, upstream explicite) | les 2 événements : mêmes quantités, même contributing, même provider_total | 🔲 |
| E2 | Streaming via proxy | B1 à travers le proxy | usage final identique au SDK ; provenance stream_final | 🔲 |
| E3 | Embeddings via proxy | A6 à travers le proxy | surface embeddings mesurée exactement (adaptateur dédié azure) | 🔲 |
| E4 | Content filter via proxy | C1 à travers le proxy | non-autoritaire des deux côtés ; http_status identique | 🔲 |

## F. Volume & endurance

| # | Cas | Déclenchement | Attendu | Statut |
|---|---|---|---|---|
| F1 | Rafale 50–100 appels courts | boucle async | zéro perte (compte appels == compte événements) ; rollup stable ; append O(1) | 🔲 |
| F2 | Session longue mixte | 30 min mêlant A/B/C | CoverageExactness honnête : ratios reflètent EXACTEMENT les incidents injectés | 🔲 |
| F3 | Relecture après crash | tuer le process mid-écriture puis relire | tail réparée, événements antérieurs intacts, écart journalisé | 🔲 |

---

## Ordre d'exécution conseillé (rendement de preuve par euro)

1. **A4, A5** — reasoning + cache o-series : les 2 hypothèses centrales, en réel sur Azure
2. **B1→B4** — la chaîne streaming complète jusqu'à la supersession réelle
3. **E1** — la double comptabilité SDK vs proxy (l'argument d'audit le plus fort)
4. **C4, D3, D4** — retry + parallélisme réels
5. le reste par section

Chaque capture réussie devient une fixture `*.REAL.json` qui remplace son équivalente
`*.SIMULATED.json` — le but final : **zéro hypothèse non confrontée sur Azure**.
