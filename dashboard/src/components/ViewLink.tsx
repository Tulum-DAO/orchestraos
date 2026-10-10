/**
 * A Link into a dashboard view that becomes plain text when the deployment hides that view
 * (runtime config hiddenViews): the words stay, the way to a Page not found goes.
 */
import { Link, type LinkProps } from 'react-router-dom';
import { isViewHidden } from '../lib/features';

export default function ViewLink({ to, className, title, children, ...rest }: LinkProps & { to: string }) {
  if (isViewHidden(to)) return <span className={className} title={title}>{children}</span>;
  return <Link to={to} className={className} title={title} {...rest}>{children}</Link>;
}
