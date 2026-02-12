export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`bg-bg-card border border-border rounded-lg ${className}`}>
      {title && (
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-medium text-text-muted">{title}</h3>
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}
