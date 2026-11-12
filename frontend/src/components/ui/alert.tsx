import * as React from 'react'

import { cn } from '../../lib/utils'

const Alert = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} role="alert" className={cn('rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900', className)} {...props} />
))
Alert.displayName = 'Alert'

export { Alert }
