import * as React from "react";
import { cn } from "@/lib/utils";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      className={cn(
        "flex h-11 w-full rounded-lg border border-zinc-800 bg-zinc-900/80 px-4 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 transition-all duration-300 focus:border-orange-500/60 focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:shadow-[0_0_16px_rgba(249,115,22,0.15)]",
        className
      )}
      ref={ref}
      {...props}
    />
  )
);
Input.displayName = "Input";

export { Input };
