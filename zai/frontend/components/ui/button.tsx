import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 disabled:pointer-events-none disabled:opacity-50 active:scale-95",
  {
    variants: {
      variant: {
        default:
          "bg-orange-500 text-black shadow-[0_0_20px_rgba(249,115,22,0.35)] hover:bg-orange-400 hover:shadow-[0_0_30px_rgba(249,115,22,0.55)] hover:scale-[1.03]",
        outline:
          "border border-orange-500/60 bg-transparent text-orange-400 hover:bg-orange-500/10 hover:border-orange-400 hover:shadow-[0_0_20px_rgba(249,115,22,0.25)] hover:scale-[1.02]",
        ghost: "text-orange-300 hover:bg-orange-500/10 hover:text-orange-200",
        secondary: "bg-zinc-800 text-zinc-100 hover:bg-zinc-700 hover:scale-[1.02]",
      },
      size: {
        default: "h-10 px-5 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-12 rounded-xl px-8 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  )
);
Button.displayName = "Button";

export { Button, buttonVariants };
