import type { AnchorHTMLAttributes } from 'react'
import { Link as ReactRouterLink } from 'react-router-dom'

// Adapter for Astryx's LinkProvider (see main.tsx): every Astryx component
// that renders a link (SideNavItem, TopNavItem, Link, Breadcrumbs, ...)
// calls this instead of a plain <a>, so client-side routing works
// everywhere without threading `as={...}` through each one individually.
// Astryx requires this to accept href/className/style/children.
export function RouterLink({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <ReactRouterLink to={href ?? '#'} {...props}>
      {children}
    </ReactRouterLink>
  )
}
