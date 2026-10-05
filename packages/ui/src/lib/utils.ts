import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Joins class names, letting later Tailwind classes override earlier ones (shadcn/ui). */
export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));
