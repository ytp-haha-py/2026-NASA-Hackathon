import type { ReactNode } from "react";

type WorkflowSectionProps = {
  id: string;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Keep children mounted while collapsed (preserves form state). */
  keepMounted?: boolean;
  children: ReactNode;
};

/** A page section the user can expand in place; replaces the old top-level tabs. */
export function WorkflowSection({
  id,
  title,
  open,
  onOpenChange,
  keepMounted = false,
  children,
}: WorkflowSectionProps) {
  return (
    <section className="workflow-section" id={id} aria-labelledby={`${id}-title`}>
      <h2 className="workflow-heading" id={`${id}-title`}>
        <button
          type="button"
          className="workflow-toggle"
          aria-expanded={open}
          aria-controls={`${id}-body`}
          onClick={() => onOpenChange(!open)}
        >
          <span>{title}</span>
          <span className="workflow-chevron" aria-hidden="true" />
        </button>
      </h2>
      <div id={`${id}-body`} className="workflow-body" hidden={!open}>
        {(open || keepMounted) && children}
      </div>
    </section>
  );
}
