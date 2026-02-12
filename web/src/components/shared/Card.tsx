export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
  noPadding = false,
}: {
  title?: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  noPadding?: boolean;
}) {
  return (
    <div className={`bg-bg-card border border-border rounded-xl overflow-hidden ${className}`}>
      {(title || action) && (
        <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
          <div>
            {title && <h3 className="text-sm font-semibold text-text-primary">{title}</h3>}
            {subtitle && <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>}
          </div>
          {action && <div>{action}</div>}
        </div>
      )}
      <div className={noPadding ? "" : "p-5"}>{children}</div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  icon,
  trend,
  className = "",
}: {
  label: string;
  value: React.ReactNode;
  icon?: React.ReactNode;
  trend?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`bg-bg-card border border-border rounded-xl p-4 ${className}`}>
      <div className="flex items-start justify-between mb-2">
        <span className="text-xs font-medium text-text-muted uppercase tracking-wide">{label}</span>
        {icon && <span className="text-text-muted">{icon}</span>}
      </div>
      <div className="text-xl font-bold font-mono">{value}</div>
      {trend && <div className="mt-1">{trend}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}
