type Pending<T> = { resolve: (value: T) => void; timer: ReturnType<typeof setTimeout> };

export class RequestCorrelator<T> {
  private readonly pending = new Map<string, Pending<T>>();

  get size(): number { return this.pending.size; }

  wait(id: string, timeoutMs: number, timeoutValue: T): Promise<T> {
    return new Promise<T>((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve(timeoutValue);
      }, timeoutMs);
      this.pending.set(id, { resolve, timer });
    });
  }

  resolve(id: string, value: T): boolean {
    const pending = this.pending.get(id);
    if (!pending) return false;
    clearTimeout(pending.timer);
    this.pending.delete(id);
    pending.resolve(value);
    return true;
  }

  resolveAll(value: T): void {
    for (const id of this.pending.keys()) this.resolve(id, value);
  }
}
