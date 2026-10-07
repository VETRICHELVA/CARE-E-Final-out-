import { TableCell } from "@care-e/ui";
import type { Product } from "../api";

export function ProductName({ product }: { product: Product | undefined }) {
  return (
    <>
      <div className="font-medium">{product?.name ?? "Unknown product"}</div>
      <div className="text-xs text-muted-foreground">{product?.code}</div>
    </>
  );
}

export function ProductCell({ product }: { product: Product | undefined }) {
  return (
    <TableCell>
      <ProductName product={product} />
    </TableCell>
  );
}
