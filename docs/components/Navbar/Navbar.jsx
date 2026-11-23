'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import Search from '../Search/Search'
import { GithubLogo, List, X, ArrowUpRight } from '@phosphor-icons/react/dist/ssr'
import { TABS, activeTab } from '../site/sections'
import CtaLink from '../cta/CtaLink'
import { GET_STARTED_URL } from '@/lib/cta'

function Navbar() {
  const pathname = usePathname()
  const current = activeTab(pathname)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const tabsRef = useRef(null)

  // On narrow screens the tab row scrolls; keep the active tab in view.
  useEffect(() => {
    const row = tabsRef.current
    const active = row?.querySelector('.lh-navbar-tab-active')
    if (!row || !active) return
    const left = active.getBoundingClientRect().left - row.getBoundingClientRect().left + row.scrollLeft
    if (left < row.scrollLeft || left + active.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollLeft = left - (row.clientWidth - active.offsetWidth) / 2
    }
  }, [current.key])

  // Close the drawer whenever the route changes.
  useEffect(() => setMobileMenuOpen(false), [pathname])

  // Prevent body scroll while the drawer is open.
  useEffect(() => {
    document.body.style.overflow = mobileMenuOpen ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [mobileMenuOpen])

  return (
    <>
      <div className="lh-navbar" role="banner" data-pagefind-ignore="all">
        <div className="lh-navbar-row">
          <div className="lh-navbar-left">
            <Link href="/" className="lh-navbar-logo" aria-label="Studytomy documentation home">
              <img src="/studytomy-logo.png" alt="Studytomy" />
            </Link>
            <span className="lh-navbar-badge">Docs</span>
          </div>

          <div className="lh-navbar-right">
            <Search className="lh-navbar-search" placeholder="Search the docs…" />
            <a
              href="https://github.com/nirzaf/studytomyportal"
              target="_blank"
              rel="noopener noreferrer"
              className="lh-navbar-icon lh-hide-mobile"
              aria-label="Studytomy Portal on GitHub"
            >
              <GithubLogo size={18} weight="fill" />
            </a>
            <CtaLink href={GET_STARTED_URL} placement="navbar" className="lh-navbar-cta lh-hide-mobile">
              Get started
            </CtaLink>
            <button
              className="lh-mobile-menu-btn"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              aria-label={mobileMenuOpen ? 'Close menu' : 'Open menu'}
            >
              {mobileMenuOpen ? <X size={20} weight="bold" /> : <List size={20} weight="bold" />}
            </button>
          </div>
        </div>

        <nav ref={tabsRef} className="lh-navbar-tabs" aria-label="Documentation sections">
          <div className="lh-navbar-tabs-inner">
            {TABS.map(({ key, href, label, icon: Icon }) => (
              <Link
                key={key}
                href={href}
                className={`lh-navbar-tab ${key === current.key ? 'lh-navbar-tab-active' : ''}`}
                aria-current={key === current.key ? 'page' : undefined}
              >
                <Icon size={15} weight={key === current.key ? 'fill' : 'regular'} />
                {label}
              </Link>
            ))}
          </div>
        </nav>
      </div>

      {mobileMenuOpen && <div className="lh-mobile-overlay" onClick={() => setMobileMenuOpen(false)} />}

      <div className={`lh-mobile-drawer ${mobileMenuOpen ? 'lh-mobile-drawer-open' : ''}`} data-pagefind-ignore="all">
        <div className="lh-mobile-drawer-links">
          <a href="https://github.com/nirzaf/studytomyportal" target="_blank" rel="noopener noreferrer" className="lh-mobile-drawer-link">
            GitHub <ArrowUpRight size={12} weight="bold" />
          </a>
          <CtaLink href={GET_STARTED_URL} placement="mobile_menu" className="lh-mobile-drawer-link">
            Get started <ArrowUpRight size={12} weight="bold" />
          </CtaLink>
        </div>
        <div className="lh-mobile-drawer-divider" />
        <div id="mobile-sidebar-mount" />
      </div>
    </>
  )
}

export default Navbar
