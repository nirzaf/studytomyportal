# Studytomy Portal — Navigation & Initial Configuration Guide

**Applicability (2026-11-17):** this is a navigation/configuration guide, not deployment or acceptance evidence. `lms.studytomy.com` is a target domain; this project remains local-only. Native administrator permissions do not bypass Studytomy managed-course, learner, guardian, content-review or policy gates. See [documentation review](docs/studytomy/DOCUMENTATION-REVIEW.md).

Overview of the Studytomy learning platform (`lms.studytomy.com`) when signed in as an Administrator, covering the six primary learning sections, top-bar shortcut icons, AI Playgrounds usage, and recommended initial pilot configuration.

These sections are designed for delivering courses, organizing learning programs, managing educational resources, encouraging collaboration, and creating AI-powered interactive activities.

---

## 1. Main navigation tabs

### 1. Courses — Create and deliver lessons
The main classroom and learning content area. Administrators and tutors can create courses, organize chapters, upload video/PDF lessons, add quizzes and assignments, generate educational content with AI, and control learner access.

- **Studytomy example**: Create *Year 10 Mathematics*, with chapters for Algebra, Geometry, Trigonometry, and assessments. Students can complete lessons, receive supported automatic marking, and see their progress.

### 2. Paths — Structured learning programs
Combine multiple courses into a guided learning journey. You can define required and optional courses, arrange their order, unlock later courses after prerequisites are completed, and award certificates.

- **Studytomy example**: A *G.C.E. A/L Combined Mathematics Preparation Path* containing *Common Unit Revision → Pure Mathematics → Exam Practice* (the practice material must be authored or licensed; a path name does not imply access to official papers).
- **Note**: These are rule-based learning paths, not automatically AI-personalized study plans.

### 3. Library — Central educational resource repository
Store and organize PDFs, worksheets, textbooks, images, videos, audio, courses, playgrounds, and other learning materials in folders. You can manage access and reuse resources across lessons.

- **Studytomy example**: Create *Grade 12 → Chemistry* under the Sri Lankan National School Syllabus and organize approved syllabus documents, revision notes, diagrams, and practical worksheets.

### 4. Podcasts — Audio-based learning
The platform includes podcast authoring and listening workflows. The checked-in demo bundle currently has no podcast episodes or bundled audio, so an empty podcast area is expected until an authorized educator adds material.

- **Studytomy example**: Add an audio lesson only when the source and recording rights are clear.
- **Note**: Availability of audio-generation tools depends on the configured provider and current organization permissions.

### 5. Communities — Student discussions and collaboration
Create subject-specific or course-specific discussion forums. Students can post questions, reply, react, and vote. Administrators can configure membership restrictions and moderation rules.

- **Studytomy example**: A *Grade 13 Chemistry Discussion Group* where students discuss difficult topics and tutors clarify misconceptions.
- **Note**: Communities are discussion forums; private direct messaging is a separate Enterprise feature.

### 6. Playgrounds — sandboxed activities and learner practice
The learner Playgrounds page provides the Practice Studio and any playgrounds available to the learner. The Studio has built-in Grade 8 mathematics practice for equivalent fractions, rectangle perimeter, and square numbers. For another topic it searches approved passages; when no passage can support practice, it offers guided recall so the learner can review notes rather than inventing source-free answers.

Existing generated playgrounds render in a sandbox. Scripts and inline event handlers are disabled, so JavaScript-dependent calculators, graphs, and recording controls will not work. Use static HTML/CSS diagrams, labels, worked examples, and explanatory text for generated activities. The momentum simulator is a separate governed build and release path; a generic playground is not a validated science laboratory.

---

## 2. Main navigation

The local learner course view was checked in the built-in browser. Its top bar showed Courses, Explore Topics, Paths, More, Search, Progress, Calendar, Boards, and Profile. The additional links exposed by More and Profile depend on the signed-in user's permissions; do not assume every role sees the same destinations.

| Icon | Actual function | What you can do |
|---|---|---|
| **Search** | Global content search | Search courses, lessons, people, communities, and other accessible resources. |
| **Progress / Trail** | Student learning history | Review enrolled courses, completion percentages, and earned certificates. |
| **Calendar** | Personal learning schedule | View assignment deadlines, scheduled chapter releases, course dates, and relevant events. |
| **Boards** | Collaborative whiteboards | Draw, write notes, add diagrams, and collaborate in real time. |
| **More** | Additional navigation | Open the destinations available to the current role. |
| **Profile** | Account menu | Access permitted account and session actions. |

> [!NOTE]
> The **Dashboard** and **Help** controls are permission-dependent; learners do not necessarily see the same options as administrators.

---

## 3. Working with the Playgrounds tab

When a new organization shows **"No playgrounds yet"**, it simply means no playground entries have been created yet.

To test it:
1. Click **New Playground**.
2. Give it a name and select **AI Knowledge** or an existing course as its source.
3. Describe the activity you want. The application will generate a preview, which you can refine and save before publishing.

### Example prompt for a sandboxed playground

```text
Create a Grade 10 Ohm’s Law study card using static HTML and CSS. Show a
labelled circuit diagram, explain voltage, resistance, and current, and include
three worked examples plus a short prediction question. Make it readable on a
phone. Do not use JavaScript, inline event handlers, or external requests.
```

Generated scripts are disabled in the preview, so script-driven calculators,
graphs and recording controls are unavailable. The governed momentum simulator
uses a separate reviewed, pinned build and release workflow.

---

## 4. Recommended initial configuration for Studytomy

*(Recommendations for initial pilot setup, not existing published content)*

| Section | Initial content to configure |
|---|---|
| **Courses** | Year 10 Mathematics and Science pilot courses |
| **Paths** | *Mathematics Foundations → Applied Practice → Assessment* |
| **Library** | Approved curriculum, worksheets, and reference material |
| **Podcasts** | Short topic-revision audio lessons |
| **Communities** | Mathematics Q&A and Science Discussion |
| **Playgrounds** | Ohm's Law, Graphing Functions, and Forces demonstrations |
| **Boards** | Collaborative mathematical problem-solving board |
| **Assessments** *(via Dashboard)* | A baseline test and topic checkpoints |

**Recommended starting step**: Create **one course**, **one assessment**, and **one interactive playground** first to demonstrate how Studytomy supports teaching, learning, AI-assisted explanation, practice, and progress tracking before expanding to the broader National School Syllabus and specialized AI features.
