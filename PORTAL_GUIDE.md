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
Create educational podcast channels, upload episodes, and publish audio lessons. Students can listen through the platform. Podcast episodes can also be embedded in courses and distributed through RSS feeds.

- **Studytomy example**: Publish *Year 12 Biology — 10-minute revision*, with short episodes about genetics, photosynthesis, and cellular respiration.
- **Note**: The platform also provides AI audio-generation tools within supported content workflows.

### 5. Communities — Student discussions and collaboration
Create subject-specific or course-specific discussion forums. Students can post questions, reply, react, and vote. Administrators can configure membership restrictions and moderation rules.

- **Studytomy example**: A *Grade 13 Chemistry Discussion Group* where students discuss difficult topics and tutors clarify misconceptions.
- **Note**: Communities are discussion forums; private direct messaging is a separate Enterprise feature.

### 6. Playgrounds — AI-generated interactive learning experiences
Particularly relevant for interactive concept demonstrations and proposed virtual laboratories. Describe an interactive activity to AI, which generates an HTML-based experience. You can preview, refine, save, publish, restrict access, and embed it in courses.

- **Studytomy examples**: An interactive Ohm's Law calculator, a graph of projectile motion, a mathematical function visualizer, or a simple chemistry demonstration.
- **Note**: Generated simulations must be scientifically validated before being used for formal practical assessments.

---

## 2. Icons on the right side of the navigation

These shortcuts appear after the search bar in [apps/web/components/Objects/Menus/OrgMenu.tsx](apps/web/components/Objects/Menus/OrgMenu.tsx):

| Icon | Actual function | What you can do |
|---|---|---|
| **Search** | Global content search | Search courses, lessons, people, communities, and other accessible resources. |
| **Progress / Trail** | Student learning history | Review enrolled courses, completion percentages, and earned certificates. |
| **Calendar** | Personal learning schedule | View assignment deadlines, scheduled chapter releases, course dates, and relevant events. |
| **Boards** | Collaborative whiteboards | Draw, write notes, add diagrams, and collaborate in real time. |
| **Dashboard** | Administration | Access course management, assignments, users, groups, organization configuration, and developer tools. |
| **Help** | Support resources | Open website and feedback options. |
| **Admin profile** | Account menu | Access permitted account and session actions. |

> [!NOTE]
> The **Dashboard** and **Help** controls are permission-dependent; learners do not necessarily see the same options as administrators.

---

## 3. Working with the Playgrounds tab

When a new organization shows **"No playgrounds yet"**, it simply means no playground entries have been created yet.

To test it:
1. Click **New Playground**.
2. Give it a name and select **AI Knowledge** or an existing course as its source.
3. Describe the activity you want. The application will generate a preview, which you can refine and save before publishing.

### Example AI Playground prompt for Studytomy

```text
Create an interactive educational simulation of Ohm's Law for Grade 10 (G.C.E. Ordinary Level) Science students under the Sri Lankan National School Syllabus. Include adjustable voltage and resistance, an illustrated circuit, live current readings calculated using I = V/R, a measurement table, a graph, and three concept-check questions. Provide Start, Reset, and Record Measurement controls. Explain the ideal-circuit assumptions and make the layout responsive.
```

> [!IMPORTANT]
> The generated activity is useful for explaining concepts interactively, but it does not automatically provide persistent experiment marks, scientifically validated 3D simulation, or integration with diagnostic mastery tracking.

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
