// One press of Send takes a prompt from the first press until the server answers (KYO-724).

/**
 * The prompt being sent, or none. A second press while one is in flight posts nothing, so a
 * prompt reaches the server once however many times Send is pressed before it answers.
 */
export class SendGate<T> {
  private inFlight: T | null = null;

  /** Takes the gate for `item`; false when another send holds it. */
  begin(item: T): boolean {
    if (this.inFlight !== null) return false;
    this.inFlight = item;
    return true;
  }

  /** The item whose send is in flight, or none. */
  get current(): T | null {
    return this.inFlight;
  }

  /** The server answered, with a hold or a refusal: Send is available again. */
  end(): T | null {
    const item = this.inFlight;
    this.inFlight = null;
    return item;
  }
}
