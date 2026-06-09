import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-orange-500/40 bg-orange-500/15 text-orange-300",
        buy: "border-emerald-500/40 bg-emerald-500/15 text-emerald-400",
        watch: "border-yellow-500/40 bg-yellow-500/15 text-yellow-400",
        monitor: "border-orange-600/40 bg-orange-600/15 text-orange-400",
        reject: "border-red-500/40 bg-red-500/15 text-red-400",
        outline: "border-zinc-700 text-zinc-300",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
