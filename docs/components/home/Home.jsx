import Link from 'next/link'
import {
  ArrowRight,
  HardDrives,
  Code,
  BookOpen,
  PencilSimpleLine,
  Sparkle,
  Users,
  CreditCard,
  GearSix,
  ArrowsClockwise,
  BracketsCurly,
  GithubLogo,
} from '@phosphor-icons/react/dist/ssr'
import './home.css'

const START = [
  {
    href: '/getting-started/quickstart',
    icon: HardDrives,
    label: 'Start here',
    title: 'Run locally',
    text: 'Start Studytomy Portal from this source checkout.',
    code: 'npx learnhouse dev',
    action: 'Quickstart',
  },
  {
    href: '/developers',
    icon: Code,
    label: 'Headless',
    title: 'Build with Studytomy',
    text: 'Embed lessons, render them in React, or drive everything from the API and SDK.',
    action: 'Developer docs',
  },
]

const TOPICS = [
  { href: '/platform/courses', icon: BookOpen, title: 'Courses', text: 'Chapters, activities, publishing' },
  { href: '/platform/editor', icon: PencilSimpleLine, title: 'Editor', text: 'Blocks, quizzes, collaboration' },
  { href: '/platform/ai', icon: Sparkle, title: 'AI', text: 'Tutoring and course assistants' },
  { href: '/platform/users', icon: Users, title: 'Users & roles', text: 'Permissions, groups, sign-in' },
  { href: '/platform/payments', icon: CreditCard, title: 'Payments', text: 'Sell courses and memberships' },
  { href: '/self-hosting/configuration', icon: GearSix, title: 'Configuration', text: 'Domains, email, storage, AI keys' },
  { href: '/self-hosting/maintenance/updates', icon: ArrowsClockwise, title: 'Updates & backups', text: 'Keep an instance current' },
  { href: '/reference', icon: BracketsCurly, title: 'API reference', text: 'Every endpoint, with a playground' },
]

export function HomeHero() {
  return (
    <section className="lhh-hero">
    <span className="lhh-label">Studytomy Portal documentation</span>
      <h1 className="lhh-title">Learn, teach, and build with Studytomy.</h1>
      <p className="lhh-lede">
        Guides for using Studytomy Portal, self-hosting the codebase, and building with its API and activity components.
      </p>
    </section>
  )
}

export function StartPaths() {
  return (
    <div className="lhh-start">
      {START.map(({ href, icon: Icon, label, title, text, code, action }) => (
        <Link key={href} href={href} className="lhh-start-card">
          <span className="lhh-chip">
            <Icon size={18} weight="bold" />
          </span>
          <span className="lhh-label">{label}</span>
          <span className="lhh-start-title">{title}</span>
          <span className="lhh-start-text">{text}</span>
          {code && <code className="lhh-start-code">{code}</code>}
          <span className="lhh-start-action">
            {action}
            <ArrowRight size={13} weight="bold" />
          </span>
        </Link>
      ))}
    </div>
  )
}

export function TopicGrid() {
  return (
    <div className="lhh-topics">
      {TOPICS.map(({ href, icon: Icon, title, text }) => (
        <Link key={href} href={href} className="lhh-topic">
          <span className="lhh-chip lhh-chip-sm">
            <Icon size={15} weight="bold" />
          </span>
          <span>
            <span className="lhh-topic-title">{title}</span>
            <span className="lhh-topic-text">{text}</span>
          </span>
        </Link>
      ))}
    </div>
  )
}

export function CommunityRow() {
  const items = [
    { href: 'https://github.com/nirzaf/studytomyportal', icon: GithubLogo, title: 'Studytomy Portal source', text: 'Browse the repository and its documentation' },
  ]
  return (
    <div className="lhh-community">
      {items.map(({ href, icon: Icon, title, text }) => (
        <a key={href} href={href} target="_blank" rel="noopener noreferrer" className="lhh-topic">
          <span className="lhh-chip lhh-chip-sm">
            <Icon size={15} weight="fill" />
          </span>
          <span>
            <span className="lhh-topic-title">{title}</span>
            <span className="lhh-topic-text">{text}</span>
          </span>
        </a>
      ))}
    </div>
  )
}
