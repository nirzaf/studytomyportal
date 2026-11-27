# Studytomy Virtual Science Lab — Pilot 01 Specification (Conservation of Linear Momentum)

> [!IMPORTANT]
> **Approval status**: **DRAFT — AWAITING QUALIFIED EDUCATOR SIGN-OFF AND RIGHTS VERIFICATION.**
> This specification defines the bounded technical, scientific, and pedagogical contract for Pilot 01. No student launch, Ministry of Education / NIE endorsement, physical-laboratory equivalence, or independent-mastery claim is authorized until an accountable qualified Sri Lankan Physics educator completes the sign-off record below.

---

## 1. Syllabus Alignment & Provenance Record

| Field | Pilot 01 Target Value |
|---|---|
| **Jurisdiction & Authority** | Sri Lanka — National Institute of Education (NIE) / Ministry of Education |
| **Curriculum Programme** | Sri Lankan National School Syllabus (Grades 6–13, English Medium) |
| **Selected Grade / Stream** | Grade 12 — G.C.E. Advanced Level (A/L) Physics *(single pilot grade; does not cover all Grades 6–13)* |
| **Instruction Language** | English (`en`) |
| **Unit & Topic** | Unit 2: Mechanics — Linear Momentum, Impulse, and Conservation of Linear Momentum in 1D Collisions |
| **Syllabus Edition / Source Reference** | *Pending qualified educator entry of exact NIE Physics Syllabus / Teacher's Instructional Manual edition & printed page range via grounded source provenance intake* |
| **Rights & Distribution Status** | Simulator engine derived from MIT-licensed `danielw-sudo/agentuto` (`697025a14aabbd391072fbedc522d1d68bebcbc4`, MIT notice preserved in `apps/labs/momentum-pilot/LICENSE`). Original worksheet authored in English for Studytomy; no paid examination papers or unverified third-party textbook scans bundled. |
| **Accountable Subject Reviewer** | *Pending assignment (`content_release_review` independent reviewer; distinct from author)* |

**Explicit non-claims**:
- Grade level (`Grade 12`) is **not** learner age, legal capacity, or guardian consent status.
- Opening the simulation, recording samples, or exporting a CSV is **not** measured mastery, **not** physical-laboratory equivalence, and **not** a G.C.E. A/L examination substitute.

---

## 2. Pinned Scientific Model, Assumptions & Parameter Bounds

- **Curated build location**: `apps/labs/momentum-pilot/`
- **Pinned experiment ID**: `conservation-momentum`
- **Upstream engine provenance**: `danielw-sudo/agentuto` commit `697025a14aabbd391072fbedc522d1d68bebcbc4` (MIT License)
- **Governing physics assumptions**:
  - Two rigid circular pucks moving on a frictionless horizontal plane (`F_ext = 0` prior to arena wall contact).
  - Exact event-time collision resolution along the line of centers with coefficient of restitution $e \in [0, 1]$.
  - System isolation (`isSystemIsolated = true`) holds **only** while `wallContactCount === 0` **and** no mid-run parameter mutation has altered body masses or velocities.
  - All telemetry channels use explicit SI units: time (`s`), position (`m`), velocity (`m/s`), mass (`kg`), linear momentum (`kg·m/s`), kinetic energy (`J`).

### Permitted Parameter Bounds

| Parameter | Symbol | Unit | Min | Max | Step | Default |
|---|---|---|---:|---:|---:|---:|
| Body 1 Mass | $m_1$ | `kg` | `0.5` | `10.0` | `0.5` | `2.0` |
| Body 2 Mass | $m_2$ | `kg` | `0.5` | `10.0` | `0.5` | `2.0` |
| Body 1 Initial Velocity | $v_{1x}$ | `m/s` | `-10.0` | `10.0` | `0.5` | `5.0` |
| Body 2 Initial Velocity | $v_{2x}$ | `m/s` | `-10.0` | `10.0` | `0.5` | `-3.0` |
| Impact Offset | $b$ | `m` | `-1.2` | `1.2` | `0.1` | `0.0` |
| Coefficient of Restitution | $e$ | dimensionless | `0.0` | `1.0` | `0.05` | `1.0` |

### Verified Reference Cases (Automated Scientific Regression)

1. **OpenStax University Physics Vol. 1, §9.3, Example 9.8 (Equal-mass head-on elastic collision)**:
   - $m_1 = m_2 = 2.0\text{ kg}$, $v_{1i} = 2.5\text{ m/s}$, $v_{2i} = 0.0\text{ m/s}$, $b = 0$, $e = 1.0$.
   - Expected post-collision (before wall contact): $v_{1f} = 0.0\text{ m/s}$, $v_{2f} = 2.5\text{ m/s}$, $P_x = 5.0\text{ kg·m/s}$ conserved, $\Delta K = 0\text{ J}$.
2. **OpenStax University Physics Vol. 1, §9.4, Example 9.11 (Unequal-mass head-on elastic collision)**:
   - $m_1 = 1.2\text{ kg}$, $m_2 = 1.5\text{ kg}$ (mass ratio $12:15$), $v_{1i} = 2.5\text{ m/s}$, $v_{2i} = 0.0\text{ m/s}$, $b = 0$, $e = 1.0$.
   - Expected post-collision (before wall contact): $v_{1f} \approx -0.277778\text{ m/s}$, $v_{2f} \approx 2.222222\text{ m/s}$, $P_x = 3.0\text{ kg·m/s}$ conserved.
3. **Perfectly Inelastic Head-On Collision ($e = 0.0$)**:
   - $m_1 = 2.0\text{ kg}$, $m_2 = 4.0\text{ kg}$, $v_{1i} = 6.0\text{ m/s}$, $v_{2i} = 0.0\text{ m/s}$, $b = 0$, $e = 0.0$.
   - Expected post-collision: $v_{1f} = v_{2f} = V_{\text{cm}} = 2.0\text{ m/s}$, $P_x = 12.0\text{ kg·m/s}$ conserved, kinetic energy dissipated $\Delta K = 24.0\text{ J}$.
4. **Coherent Single-Condition Recording Invariant (F-03 Guard)**:
   - Changing `mass1` from `2.0` to `4.0` during or after a recording **must not** mix momenta (`4` and `14 kg·m/s`) under one parameter header or falsely claim `isSystemIsolated = true`.

---

## 3. English Student Worksheet & Assessment Contract

### Learning Objective
Investigate how total linear momentum ($P_{\text{total}} = m_1 v_1 + m_2 v_2$) and total kinetic energy ($K_{\text{total}} = \tfrac{1}{2}m_1 v_1^2 + \tfrac{1}{2}m_2 v_2^2$) behave across elastic ($e = 1.0$) and inelastic ($e = 0.0$) head-on collisions in an isolated system, and explain the effect of external wall impulses.

### Student Practical Procedure
1. Open the authorized **Conservation of Linear Momentum** Virtual Lab inside your managed Studytomy Physics release.
2. **Trial 1 (Elastic, $e = 1.0$)**: Set $m_1 = 2.0\text{ kg}$, $m_2 = 2.0\text{ kg}$, $v_{1i} = 4.0\text{ m/s}$, $v_{2i} = 0.0\text{ m/s}$, $b = 0.0\text{ m}$, $e = 1.0$. Start recording, run until after the first puck-to-puck collision (before wall contact), pause, and export Trial 1 CSV.
3. **Trial 2 (Inelastic, $e = 0.0$)**: Reset/start a new run with $m_1 = 2.0\text{ kg}$, $m_2 = 4.0\text{ kg}$, $v_{1i} = 6.0\text{ m/s}$, $v_{2i} = 0.0\text{ m/s}$, $b = 0.0\text{ m}$, $e = 0.0$. Record through collision before wall contact, pause, and export Trial 2 CSV.
4. **Trial 3 (Wall impulse observation)**: Continue Trial 2 until a puck strikes the arena wall and observe `isSystemIsolated` changing to `false` and total momentum changing.
5. Open the linked **Studytomy Practical Assignment**:
   - Enter numerical answers (computed post-collision velocities, total momentum, and kinetic energy lost) in the existing `NUMBER_ANSWER` tasks.
   - Submit a single **PDF practical report** or a **ZIP archive** containing your Trial 1 & Trial 2 exported CSV files plus written observations/conclusions in the `FILE_SUBMISSION` task.

### Accepted Submission Contract & Assistance Policy
- **Accepted upload formats**: Existing Studytomy `FILE_SUBMISSION` allowlist (`PDF`, `ZIP`, images, office documents; up to 50 MiB). Direct raw `.csv` upload is **intentionally rejected (HTTP 415)** in Pilot 01; bundle exported CSV files inside a `.zip` archive or embed tabulated readings in a `.pdf` practical report.
- **Assistance policy**: Standalone browser AI and API-key prompts are **completely removed** from the student lab build. Students work without embedded AI inside the simulator; any future lab-aware AI must go through server-controlled Studytomy tutoring with guardian consent and assessment-help restrictions.
- **Manual Teacher Rubric (10 marks total — human-graded, not auto-promoted to independent mastery)**:
  1. Experimental setup & coherent single-condition readings for Trials 1 and 2 (3 marks).
  2. Correct momentum calculations ($P_{\text{before}}$ vs $P_{\text{after}}$ with SI units `kg·m/s`) (3 marks).
  3. Kinetic energy comparison between elastic ($e = 1.0$) and inelastic ($e = 0.0$) collisions with SI units (`J`) (2 marks).
  4. Accurate explanation of isolated-system breakdown upon external wall contact (2 marks).

---

## 4. Public-vs-Protected Access Boundary Decision

- **Static simulation bundle**: Contains **zero learner data, zero institutional secrets, and zero AI credentials**. Serving the pinned static bundle from a dedicated static origin (e.g., `labs.studytomy.com`) with `Content-Security-Policy: frame-ancestors 'self' https://lms.studytomy.com` is commercially acceptable for Pilot 01 **only** because course instructions, curriculum context, worksheets, assignment submissions, grades, and feedback remain strictly inside authenticated Studytomy managed releases.
- **Managed release immutability**: Studytomy binds the exact `lab_id`, `build_version`, `build_sha256`, `experiment_id`, and `entry_url` inside the reviewed release snapshot. Changing the lab descriptor or build digest invalidates prior content review.

---

### Drafting a managed lab

In the managed course editor, open an Embed activity and select a simulator build
from the **Virtual science lab** panel. Save it as a draft. The API supplies
choices from its reviewed manifests and configured origins and validates the
descriptor again before saving. Existing linked assignment settings are kept
when changing the pinned build. The saved draft goes through the existing
independent review and release workflow. Learners open the released simulator
from their learning account, where the sandbox allows fullscreen and downloads.

## 5. Educator Sign-Off Record *(Required Before Student Admission)*

- [ ] Qualified Sri Lankan A/L Physics educator verified syllabus edition, objective alignment, and SI notation.
- [ ] Independent `content_release_review` reviewer verified worked examples, parameter bounds, and manual rubric.
- [ ] Safeguarding / privacy owner verified zero browser AI calls, temporary-only notebook state, and approved framing headers.
- [ ] Real-stack staging verification (PostgreSQL + browser learner→lab→assignment→feedback journey) completed and archived.
