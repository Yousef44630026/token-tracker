# Présentation théorique — les concepts, et le code qui les implémente

À montrer à l'écran dans l'ordre. Chaque concept = une idée de design + le fichier exact à ouvrir
(`Ctrl+P` puis le nom du fichier).

---

## 1. Le problème : l'additivité dépend du fournisseur

Un appel LLM renvoie plusieurs compteurs de tokens. Le piège : ils ne se combinent pas de la
même façon selon le fournisseur.
- OpenAI/Azure : `cached_tokens` est **une partie de** `input_tokens` (déjà dedans).
- Anthropic : les tokens de cache sont **des buckets séparés**, à additionner.

Additionner naïvement → on double-compte. C'est le bug réel de Langfuse (#12306) et LiteLLM
(#14849). Toute l'architecture répond à ce problème : **stocker la relation d'additivité, ne
jamais la deviner.**

---

## 2. Le modèle de données : un événement = un sac de quantités typées

**Théorie** : on ne stocke pas « un nombre de tokens ». On stocke, par appel, un `TokenEvent`
contenant plusieurs `TokenQuantity`. Chaque quantité porte trois informations orthogonales :
*ce que c'est* (token_type), *à quel point c'est mesuré* (precision), et *comment ça se combine*
(overlap × trust).

**Code** : `tracker\models\token_quantity.py` (la classe et ses champs stockés, en haut) et
`tracker\models\token_event.py` (l'événement = `quantities: list[TokenQuantity]`).

---

## 3. Le cœur théorique : deux axes orthogonaux — overlap × trust

**Théorie** : la question « est-ce que je compte cette quantité ? » a **deux raisons
indépendantes** de répondre non. Soit c'est une sous-partie d'un autre compteur (overlap), soit
on n'a pas vérifié sa sémantique (trust). Les séparer rend le raisonnement explicite.

**Code** : `tracker\models\enums.py`
```python
class Overlap(str, Enum):
    INDEPENDENT = "independent"   # se suffit à lui-même ; éligible à la somme
    SUBTOTAL_OF = "subtotal_of"   # déjà à l'intérieur d'un parent (cached_input dans input)

class Trust(str, Enum):
    VERIFIED    # l'adaptateur a confirmé comment ce compteur se relie au total
    UNVERIFIED  # sémantique non prouvée -> exclu par prudence
```

---

## 4. La pureté du token_type (INV-3)

**Théorie** : `token_type` dit **ce que sont** les tokens (input, output, cached_input,
reasoning…), **jamais** comment ils ont été mesurés. Un output estimé reste `token_type=output`
avec `precision=estimate` — pas un type « output_estimé ». Sinon la mesure pollue la comptabilité.

**Code** : l'énumération `TokenType` dans `tracker\models\enums.py` (aucun type « estimé » ou
« partiel »).

---

## 5. La table de vérité par fournisseur (INV-4) — la décision centrale

**Théorie** : l'additivité n'est **jamais déduite** du nom du type. Elle est **assignée
explicitement par fournisseur**, dans une seule table centrale. Un couple (fournisseur, type)
inconnu tombe en `unverified` (fail-closed) : compté 0, signalé — jamais deviné.

**Code** : `tracker\normalization\additivity.py`
```python
("openai", TokenType.INPUT):        (TOTAL_CONTRIBUTING, None),
("openai", TokenType.CACHED_INPUT): (SUBTOTAL_OF, "input"),    # cache = DANS input
("openai", TokenType.REASONING):    (SUBTOTAL_OF, "output"),   # reasoning = DANS output
# ...
"azure_openai": "openai"   # alias : Azure EST OpenAI côté comptabilité
```

---

## 6. La frontière stocké / dérivé (INV-1 & INV-2) — pourquoi ça ne peut pas mentir

**Théorie** : le stockage ne contient **que des faits source**. Tout ce qui est un total est
**recalculé à la lecture**, jamais écrit sur disque. Conséquence : le stockage ne peut jamais
être en désaccord avec la règle de comptage — il n'y a pas de total figé à corriger.

**Code** : `tracker\models\token_quantity.py`
```python
@property
def included_in_total(self) -> bool:
    # sommé seulement si indépendant ET vérifié ET connu
    return self.overlap == Overlap.INDEPENDENT and self.trust == Trust.VERIFIED and self.quantity is not None

@property
def quantity_in_total(self) -> int:
    return self.quantity if self.included_in_total else 0   # un subtotal -> 0
```
Un `cached_input` a `overlap == SUBTOTAL_OF` → `included_in_total = False` → **compte pour 0**.
C'est mécanique. (Montre aussi que `to_dict()`, en bas du fichier, ne sérialise PAS ces
propriétés — elles sont recalculées.)

---

## 7. Le signal de justesse : l'identité de réconciliation

**Théorie** : si notre comptage est correct, alors `somme(ce qu'on compte) == total rapporté par
le fournisseur`. On expose l'écart. **Zéro écart = comptage prouvé exact.** C'est vérifiable sur
chaque événement, sans confiance aveugle.

**Code** : `tracker\models\token_event.py`
```python
@property
def event_contributing_tokens(self) -> int:
    return 0 if self.superseded or not self.is_authoritative else self._sum_quantity_in_total

@property
def event_total_mismatch(self) -> int | None:
    return self.provider_total_tokens - self._sum_quantity_in_total   # 0 = exact
```

---

## 8. Inconnu n'est pas zéro (INV-6)

**Théorie** : un token perdu (stream coupé, usage absent) est `quantity=None`,
`precision=unknown` — **jamais un zéro confiant**. Un zéro dirait « j'ai mesuré 0 » ; l'inconnu
dit « je ne sais pas », et c'est compté comme un inconnu, pas noyé dans un total.

**Code** : l'énumération `PrecisionLevel` (exact / estimate / unknown) dans
`tracker\models\enums.py`, et la branche `unknown` de `export_warning` (token_quantity.py).

---

## 9. La supersession (INV-5) : streams partiels sans double-compte

**Théorie** : un stream interrompu produit une estimation, puis l'usage final réel arrive. Les
deux sont **appariés par `request_correlation_id`** (pas par span — un span peut contenir des
retries). Le partiel est marqué *superseded* et compte 0. On ne compte que le final.

**Code** : `tracker\normalization\supersession.py` (la fonction `reconcile_supersession`).

---

## 10. La cohérence du récit

Ces neuf décisions forment un tout : **stocker les faits + la relation d'additivité par
fournisseur, tout recalculer à la lecture, échouer fermé sur l'inconnu, et se vérifier par
réconciliation.** C'est ce qui rend le comptage exact et auditable là où les outils orientés
« coût d'abord » double-comptent.

---

## Le parcours pour la tutrice — 3 fichiers, 3 idées

1. **`tracker\normalization\additivity.py`** — « voici la décision : qui compte, qui est une
   sous-partie, par fournisseur. »
2. **`tracker\models\token_quantity.py` (L112)** — « voici la conséquence : une sous-partie
   compte pour 0, et ce n'est jamais stocké. »
3. **`tracker\models\token_event.py` (L124)** — « voici la preuve : `mismatch = 0` signifie que
   ma somme égale le total du fournisseur. »

Et pour dire « ce n'est pas que de la théorie » : ouvre un test qui l'exerce sur données réelles
— `tests\test_azure_real_matrix.py` (ligne « GROUND TRUTH: cache is not double-counted »).
