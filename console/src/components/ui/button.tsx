import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "border border-transparent text-[19px] font-bold leading-none text-primary-foreground [background-clip:padding-box,border-box] [background-image:linear-gradient(var(--primary),var(--primary)),var(--gradient-meteor)] [background-origin:border-box] hover:[background-image:linear-gradient(var(--cosmic-blue),var(--cosmic-blue)),var(--gradient-meteor)]",
        secondary:
          "border border-border bg-secondary text-sm font-medium text-secondary-foreground hover:bg-secondary-hover",
        outline:
          "border border-border bg-transparent text-sm font-medium text-foreground hover:bg-secondary",
        ghost: "text-sm font-medium hover:bg-secondary hover:text-foreground",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, children, ...props }, ref) => {
    const classes = cn(buttonVariants({ variant, size, className }));

    if (asChild && React.isValidElement(children)) {
      return React.cloneElement(children as React.ReactElement<{ className?: string }>, {
        className: cn(classes, (children.props as { className?: string }).className),
      });
    }

    return (
      <button className={classes} ref={ref} {...props}>
        {children}
      </button>
    );
  },
);

Button.displayName = "Button";
