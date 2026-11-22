'use client'

import Link from 'next/link'

const links = [
  { label: 'Documentation', href: '/' },
  { label: 'API Reference', href: '/reference' },
  { label: 'GitHub', href: 'https://github.com/nirzaf/studytomyportal' },
]

export default function Footer() {
  return (
    <footer className="lh-footer" data-pagefind-ignore="all">
      <div className="lh-footer-container">
        <p className="lh-footer-copyright">
          &copy; {new Date().getFullYear()} Studytomy
        </p>
        <nav className="lh-footer-nav">
          {links.map((link) => {
            const isExternal = link.href.startsWith('http')
            const Tag = isExternal ? 'a' : Link
            const props = isExternal
              ? { href: link.href, target: '_blank', rel: 'noopener noreferrer' }
              : { href: link.href }
            return (
              <Tag key={link.label} {...props} className="lh-footer-link">
                {link.label}
              </Tag>
            )
          })}
        </nav>
      </div>
    </footer>
  )
}
