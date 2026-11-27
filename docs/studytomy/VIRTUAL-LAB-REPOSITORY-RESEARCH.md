# Virtual-lab repository research and adoption reference

**Research snapshot:** 27 November 2026<br>
**Studytomy checkout reviewed:** `main` at `b7e57929eba1230577fe23767aac162fefccf80d`<br>
**Status:** candidate inventory only; no new simulation source is approved or integrated by this document.

This document records the supplied open-source virtual-lab research beside the existing lab implementation notes so future work can evaluate candidates against actual Studytomy requirements. The repository, its code, and its educational content have different rights boundaries. Studytomy's open-source status does not grant rights to third-party code, assets, datasets, videos, exam questions, or scientific content. A repository license must be checked at the exact revision and does not automatically cover material with separate notices.

The linked repositories' default branches can change. The license statements below are the research report's descriptions, not legal clearance. Before copying code or content, record an immutable commit SHA and inspect the root and per-directory license notices, asset provenance, transitive dependencies, and applicable content terms. An absent, ambiguous, noncommercial, or content-only license means **do not copy** until the owner supplies compatible permission.

## Candidate inventory

| Candidate | Reported focus | Research disposition | Required checks before reuse |
|---|---|---|---|
| [Agentuto](https://github.com/danielw-sudo/agentuto) | Physics and mathematics simulations, measurement, experiment recording | Primary physics candidate. The supplied report says MIT. Studytomy already carries a curated conservation-of-momentum build; see the [Momentum Pilot 01 specification](VIRTUAL-LAB-MOMENTUM-PILOT-SPEC.md) for its pinned source and local license record. | Recheck the exact upstream revision, notices, simulation-specific assets, dependency licenses and model behavior before importing another experiment. Keep AI calls on Studytomy's protected server path. |
| [ScienceLab 3D](https://github.com/rudra496/sciencelab3d) | Physics, chemistry, biology and mathematics visualizations | Multidisciplinary candidate; the supplied report and current project description identify MIT, but this is not clearance for every bundled asset or dataset. | Separate deterministic scientific models from visual demonstrations; check fonts, textures, models, data and nested licenses; run and validate each selected experiment. |
| [Valence](https://github.com/CommonLab-App/Valence) | Chemistry and molecular visualization | Chemistry candidate. The supplied report labels the code MIT; exact current license coverage and source-data terms remain to be verified. | Inspect license files and source citations at the chosen SHA, chemical data rights, safety text, calculation provenance and dependencies. No educator or safety review is implied. |
| [3ducation](https://github.com/atbt-ops/3ducation) | Foundational school science and mathematics | Younger-grade candidate. The supplied report labels it MIT and identifies Firebase-backed account/sync code. | Reuse only separately reviewed simulation modules; exclude Firebase/auth/sync and any external service integration. Audit assets, datasets and dependency licenses. |
| [Cambridge Chemistry Lab](https://github.com/nsriram/chem_lab) | Cambridge AS-level chemistry practicals | The supplied report describes MIT software and separately flags examination-paper content. Treat the questions and marking content as unavailable for reuse until rights are established. | Review code license separately from every paper, question, diagram, procedure and rubric; create original Studytomy assessment content unless explicit rights permit reuse. |
| [myPhysicsLab](https://github.com/myphysicslab/myphysicslab) | Documented mechanics and physics models | The supplied report describes Apache-2.0 licensing and useful model documentation. | Verify the current license file and required notices at the selected SHA, then test equations, units, numerical bounds and browser integration. |
| [AI Science Simulations](https://github.com/ai-science-sims/science-simulations) | Individual physics, chemistry and biology demos | The supplied report describes MIT licensing and standalone HTML simulations. | Verify the exact license and bundled assets; AI-assisted authoring is not scientific validation or an integrated tutor. Validate each simulation and its sources. |
| [Concord Consortium Lab](https://github.com/concord-consortium/lab) | Molecular and scientific modelling framework | Candidate for selected models, not a default platform replacement. The supplied report describes MIT, but the current root `LICENSE` URL was not verified in this review. | Do not copy until the applicable license is located and confirmed. Audit the older runtime, individual model rights, model data and attribution. |
| [GeniBlocks](https://github.com/concord-consortium/geniblocks) | Biology and genetics learning components | Biology candidate. The supplied report describes MIT; the current root `LICENSE` URL was not verified in this review. | Do not copy until license coverage is confirmed. Audit dependencies, third-party assets and genetics data; independently review the educational model. |
| [BN Lab Simulations](https://github.com/4mser/bn-lab-simulations) | Advanced physics and mathematics simulations | Enrichment candidate. The supplied report describes MIT and numerical validation material. | Verify source, validation procedures, license and assets for each chosen experiment. Do not treat published validation claims as Studytomy verification. |

The supplied report also identified [PhET](https://phet.colorado.edu/), [All Science Sims](https://github.com/listyantidewi1/all-science-sims), [IIT Virtual Labs](https://github.com/virtual-labs), and [NetLogo models](https://github.com/NetLogo/models) as needing special licensing review, including noncommercial or per-project terms. Do not include these materials in Studytomy's commercial or redistributable bundles unless the exact source, asset, and use are covered by an explicit compatible license or separate permission.

## AI-tutoring references

The report lists [XIVIX Physics Virtual Lab](https://github.com/XIVIX-Official/physics-virtual-lab), [LabAI](https://github.com/faaad766/LABAI), [STEMulator](https://github.com/ngulley/stemulator-ui), and [SimStep](https://zoekaputa.github.io/simstep/) as reference projects, not adoption candidates. In particular:

- Never copy a frontend-embedded provider API key or call an AI provider directly from the learner browser.
- LabAI's reported license does not provide a usable grant, and its architecture uses Supabase; neither is approved for this repository.
- Other AI tutor code requires the same license, privacy, age/consent, provider, security and content-review checks as any other source.
- Simulation values, equations, scoring and safety facts must come from deterministic, validated code and reviewed source material. Model text cannot create measurements, permissions, grades or scientific approval.

## Existing Studytomy integration points

These are existing paths to inspect before proposing a new platform or service:

- [`apps/labs/momentum-pilot/`](../../apps/labs/momentum-pilot/) — the current curated static simulation pilot.
- [`apps/api/src/services/studytomy/virtual_labs.py`](../../apps/api/src/services/studytomy/virtual_labs.py) — manifest registry, build identity and descriptor validation.
- [`apps/web/components/Objects/Account/subpages/AccountLearning.tsx`](../../apps/web/components/Objects/Account/subpages/AccountLearning.tsx) — sandboxed learner renderer.
- [`apps/api/src/services/studytomy/adaptive_teaching.py`](../../apps/api/src/services/studytomy/adaptive_teaching.py) — existing tutoring and reviewed-content fallback.
- [Momentum Pilot 01 specification](VIRTUAL-LAB-MOMENTUM-PILOT-SPEC.md) — current source provenance, scientific model, access boundary and approval checklist.

The current governed pilot is the conservation-of-momentum experiment. The broader experiment catalogue and experiment-aware AI guidance remain future work. This research does not change the approved lesson, curriculum, laboratory registry, manifest allowlist, access controls, release workflow or student availability.

## Adoption sequence

For any future candidate, use this sequence before implementation:

1. Map one requested educational outcome to an exact syllabus objective and experiment; reject candidates that do not meet the requirement.
2. Pin the upstream repository commit. Inspect every copied file's license/notice, nested assets, data, documentation excerpts and dependency tree.
3. Confirm the code and all selected material permit Studytomy's intended commercial and redistribution use. Record attribution and source provenance.
4. Review the scientific model, assumptions, parameter bounds, units, error behavior and numerical regressions; do not infer scientific validity from project popularity or visual quality.
5. Check compatibility with the current manifest registry, sandbox, CSP, offline/static bundle and no-new-service architecture.
6. Review student privacy, provider transfer, age and guardian consent, retention and safeguarding. AI keys stay server-side; default behavior remains disabled unless approved.
7. Obtain the required curriculum, qualified educator, rights, safety and safeguarding approvals. A test fixture or open-source code license is not an approval.
8. Add a narrowly pinned, reviewed build through existing release controls and test learner authorization, isolation, browser behavior and rollback.

### Current recommendation

Use the existing Agentuto-derived momentum pilot as the physics implementation baseline. Evaluate Agentuto, myPhysicsLab and BN Lab for additional physics only against an approved syllabus checklist. Evaluate Valence for chemistry and ScienceLab 3D/3ducation for broader demonstrations only after the exact code and content rights and scientific review are recorded. Do not bulk-import experiments, add a second lab platform, or enable experiment-aware AI based on this inventory alone.

## Research limits

This is a reference inventory, not a legal opinion, security review, curriculum mapping, educator approval, or evidence that the listed repositories were built or scientifically validated in Studytomy. The external projects were not integrated or executed as part of this documentation change. The original recommendation table is retained as a lead list; candidate ranking and license claims must be rechecked at the immutable revision selected for any future work.
