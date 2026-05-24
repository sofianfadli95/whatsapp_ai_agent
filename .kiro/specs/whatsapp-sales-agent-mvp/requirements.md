# Requirements Document

## Introduction

The WhatsApp Sales Agent MVP is an AI-powered conversational commerce backend that lets online sellers automate sales and customer service through WhatsApp. Customers interact through WhatsApp, and the system answers product questions, recommends products, builds carts, creates orders, generates payment links, processes payment callbacks, prepares simulated shipments, and escalates to human admins when needed.

This MVP delivers an end-to-end flow using FastAPI for HTTP and webhook handling, LangGraph for agent orchestration with persistent checkpoints, LangChain for LLM and RAG components, and PostgreSQL with pgvector for storage and product retrieval. Payment and logistics are implemented as sandbox/simulated providers in the MVP, but the system enforces the full transactional safety rules required for production: the LLM is never the source of truth for prices, stock, payment status, order status, or shipment status; orders require explicit customer confirmation; shipments are only prepared after verified payment; and payment processing is idempotent.

The MVP is designed to be deployable on Cloud Run with environment-based configuration (including a configurable LLM provider behind a model factory) and to be developed locally using `uv` for virtual environment and dependency management.

## Glossary

- **Agent**: The LangGraph-orchestrated AI agent that processes customer messages, calls tools, and produces replies.
- **API_Service**: The FastAPI application that exposes HTTP endpoints, including webhook receivers and health checks.
- **Audit_Logger**: The component that persists structured records of important tool calls and agent decisions.
- **Cart**: A persistent collection of `Cart_Items` belonging to a single `Customer`, representing items intended for purchase.
- **Cart_Item**: A line item in a `Cart` referencing a `Product`, a `Product_Variant`, a quantity, and a unit price snapshot.
- **Catalog_Service**: The backend service responsible for product catalog truth, search, and retrieval.
- **Checkpointer**: The LangGraph persistent checkpoint mechanism that stores agent conversation state in PostgreSQL.
- **Conversation**: A persistent session associated with a single `Customer` (identified by WhatsApp phone number) used to maintain agent state across messages.
- **Conversation_State**: The serialized LangGraph state for a `Conversation`, including messages, cart references, and pending actions.
- **Customer**: An end user identified by a WhatsApp phone number who interacts with the agent.
- **Escalation_Flag**: A boolean state on a `Conversation` indicating that further automated agent replies must be suppressed and a human admin must handle the conversation.
- **LLM_Factory**: The configuration component that selects and instantiates the chat model based on the `LLM_Provider` and `LLM_Model` environment variables.
- **LLM_Provider**: The configured large language model vendor (`openai`, `anthropic`, or `google`) selected via environment variable.
- **Logistics_Service**: The backend service that creates simulated shipments and tracking numbers after successful payment.
- **Order**: A deterministic record of a confirmed purchase containing line items, total amount, currency, and status.
- **Order_Service**: The backend service responsible for creating orders, computing totals, and managing order status transitions.
- **Payment**: A record of a payment attempt for an `Order`, including provider reference, status, and verification metadata.
- **Payment_Provider**: The third-party payment system (sandbox/dummy in the MVP) that issues payment links and emits payment status webhooks.
- **Payment_Service**: The backend service responsible for creating payment links and processing verified payment callbacks.
- **Payment_Webhook_Verifier**: The component that validates the authenticity of incoming payment webhooks (e.g., signature check) before any state mutation.
- **Product**: A catalog entity representing an item that can be sold.
- **Product_Variant**: A specific purchasable variation of a `Product` (e.g., size, color) with its own SKU, price, and stock count.
- **RAG_Retriever**: The component that retrieves relevant `Product` and FAQ context from the pgvector store for the agent.
- **System**: The WhatsApp Sales Agent MVP application as a whole, including its API, agent, services, repositories, workers, and storage.
- **Tool**: A LangChain-compatible callable exposed to the `Agent` that wraps a backend service action with a validated input/output schema.
- **WhatsApp_Gateway**: The Node.js + Baileys microservice that owns the WhatsApp Web session, handles QR-code pairing, persists auth state to disk (`WHATSAPP_GATEWAY_AUTH_DIR`), forwards inbound messages to the Python backend via `POST /internal/whatsapp/inbound` (bearer-token authenticated), and exposes `POST /send` for the Python backend to deliver outbound replies.
- **WhatsApp_Provider**: The Node.js + Baileys Gateway microservice that maintains a persistent WhatsApp Web WebSocket session, handles QR-code pairing, and acts as the WhatsApp transport layer for the system.
- **WhatsApp_Webhook_Verifier**: The component that validates the authenticity of incoming requests on the internal inbound channel by verifying the `Authorization: Bearer <WHATSAPP_GATEWAY_INTERNAL_TOKEN>` header (constant-time comparison) before any processing.
- **Worker**: An asynchronous background processor that consumes queued tasks (e.g., agent runs, outbound replies) outside the synchronous webhook request path.

## Requirements

### Requirement 1: WhatsApp Transport via Baileys Gateway

**User Story:** As an online seller, I want the system to receive customer WhatsApp messages through a Baileys-based gateway and send replies back through the same gateway, so that customers can converse with the agent on their preferred channel using the WhatsApp Web protocol.

#### Acceptance Criteria

1. The WhatsApp_Gateway SHALL connect to WhatsApp Web using the Baileys library and maintain a persistent WebSocket session, storing auth state to the directory configured by `WHATSAPP_GATEWAY_AUTH_DIR` so that the session survives process restarts without requiring re-pairing.
2. WHEN the WhatsApp_Gateway has no persisted auth state or the session has been logged out, it SHALL generate a QR code and expose it via `GET /qr` (bearer-token authenticated with `WHATSAPP_GATEWAY_INTERNAL_TOKEN`) as a JSON object `{"qr_data_url": "data:image/png;base64,..."}` so that an operator can scan it to pair the session.
3. WHEN the WhatsApp_Gateway is already paired and connected, the `GET /qr` endpoint SHALL return `{"status": "connected"}` instead of a QR data URL.
4. WHEN the WhatsApp_Gateway detects a socket disconnection that is not a `loggedOut` status code, it SHALL automatically attempt to reconnect without operator intervention; WHEN the status code is `loggedOut`, it SHALL stop reconnecting and expose a new QR code.
5. WHEN the WhatsApp_Gateway receives an inbound text message from a customer, it SHALL forward the message to the Python backend by calling `POST {WHATSAPP_BACKEND_INBOUND_URL}` with `Authorization: Bearer <WHATSAPP_GATEWAY_INTERNAL_TOKEN>`, a JSON body conforming to the `InboundEvent` schema (Baileys message id, sender JID normalized to E.164, message type, text body, event timestamp), and a request timeout of 10 seconds.
6. IF an inbound message with the same Baileys message id (`key.id`) and sender JID (`key.remoteJid`) is received more than once within the gateway process lifetime, THEN the WhatsApp_Gateway SHALL forward it to the backend only once and SHALL NOT emit duplicate `InboundEvent` payloads.
7. IF the Python backend returns a non-success response or the forwarding attempt exceeds 10 seconds, THEN the WhatsApp_Gateway SHALL retry with exponential backoff (1 second initial delay, doubling, capped at 8 seconds, up to 3 total attempts); on exhausted retries it SHALL persist the event to an on-disk overflow queue and re-attempt delivery every 30 seconds until the backend is reachable.
8. The Python backend SHALL expose `POST /internal/whatsapp/inbound` (bearer-token authenticated with `WHATSAPP_GATEWAY_INTERNAL_TOKEN`) that accepts `InboundEvent` payloads; it SHALL respond within 5 seconds, deduplicate by Baileys message id, persist the inbound message, and enqueue an agent task.
9. WHEN the Python backend needs to send a reply to a customer, it SHALL call `POST {WHATSAPP_GATEWAY_URL}/send` with `Authorization: Bearer <WHATSAPP_GATEWAY_INTERNAL_TOKEN>`, a JSON body `{"to": "<E.164>", "body": "<text>", "idempotency_key": "<key>"}`, and a per-attempt timeout of 10 seconds; the gateway SHALL return 200 `{"status": "sent", "message_id": "..."}` on success.
10. IF the `POST /send` call returns a non-success response or the per-attempt 10-second timeout is exceeded, THEN the Python backend SHALL retry with exponential backoff (1 second initial delay, doubling, capped at 8 seconds, up to 3 total attempts), persist the failure in the audit log with attempt count and last error indication, and SHALL NOT raise an unhandled error to the inbound handler.
11. WHEN the WhatsApp_Gateway receives a non-text inbound message (image, audio, video, document, sticker, or location), it SHALL forward the event with message type set to the appropriate non-text value; the Python backend SHALL persist the inbound message, respond 200 within 5 seconds, and the Agent SHALL reply that only text messages are supported in the MVP.
12. The WhatsApp_Gateway SHALL expose `GET /healthz` (no auth required) returning 200 always, and `GET /readyz` (no auth required) returning 200 only when the Baileys session is connected and 503 `{"status": "not_connected"}` otherwise.

### Requirement 2: Conversation State Management with LangGraph Checkpointing

**User Story:** As a customer, I want the agent to remember our previous messages, so that the conversation feels continuous across multiple WhatsApp exchanges.

#### Acceptance Criteria

1. THE System SHALL identify a unique `Conversation` for each `Customer` using the `Customer` WhatsApp phone number normalized to E.164 format with country code (8 to 15 digits total).
2. IF an inbound message arrives with a sender phone number that cannot be normalized to valid E.164 format, THEN THE System SHALL reject the message with a structured error in the audit log and SHALL NOT create a `Conversation` for it.
3. WHEN an inbound message is processed for a normalized phone number that has no existing `Conversation`, THE System SHALL create a new `Conversation` record within 5 seconds before invoking the Agent, and IF the creation operation fails or exceeds 5 seconds, THEN THE System SHALL surface the failure with conversation identifier and error indication for retry handling and SHALL NOT invoke the Agent for that message.
4. WHEN the Agent completes a graph step, THE Checkpointer backed by PostgreSQL SHALL persist the resulting Conversation_State within 5 seconds.
5. WHEN the Agent is invoked for an existing `Conversation`, THE Checkpointer SHALL load the latest Conversation_State (selected by most recent commit timestamp) for that conversation thread within 5 seconds before the graph executes.
6. WHEN the Agent finishes processing a single inbound message, THE Checkpointer SHALL commit the resulting Conversation_State to PostgreSQL within 5 seconds before the outbound reply is sent.
7. IF loading Conversation_State from the Checkpointer fails (database error, connection error, or operation exceeding 5 seconds), THEN THE Agent SHALL first set the Escalation_Flag on the `Conversation`, and only after the Escalation_Flag is successfully persisted SHALL the Agent log the failure to the Audit_Logger and stop automated processing for that message.
8. IF setting the Escalation_Flag fails (database error, connection error, or operation exceeding 5 seconds) after a Checkpointer load failure, THEN THE Agent SHALL NOT write the audit log entry, SHALL NOT mark automated processing as stopped, and the System SHALL surface the failure with conversation identifier and error indication so the message handler returns a non-success response and the inbound webhook is re-delivered.
9. IF committing Conversation_State to the Checkpointer fails (database error, connection error, or operation exceeding 5 seconds), THEN THE System SHALL NOT send the outbound reply, SHALL set the Escalation_Flag on the `Conversation`, and SHALL surface the failure with a non-success response so the message handler can retry on re-delivery.
10. THE System SHALL preserve Conversation_State across process restarts by relying solely on PostgreSQL-backed checkpoints rather than in-memory state.

### Requirement 3: Product Catalog Search and Q&A with RAG

**User Story:** As a customer, I want to ask questions about products in natural language, so that I can find suitable items without browsing a website.

#### Acceptance Criteria

1. THE Catalog_Service SHALL store `Product` and `Product_Variant` records in PostgreSQL as the source of truth for product data.
2. THE System SHALL maintain a pgvector index of `Product` descriptions and FAQ documents used by the RAG_Retriever, and SHALL update the index to reflect catalog changes within 5 minutes of the corresponding `Product` or FAQ row being created or updated.
3. WHEN the RAG_Retriever is queried with a customer question, THE RAG_Retriever SHALL return within 2 seconds up to a configurable `RAG_TOP_K` (default 5, valid range 1 to 20) most relevant `Product` and FAQ chunks whose vector similarity meets or exceeds the configured `RAG_SIMILARITY_THRESHOLD` (default 0.7, valid range 0.0 to 1.0).
4. THE Agent SHALL answer product questions using only content returned by the RAG_Retriever and `Tool` responses from the Catalog_Service.
5. IF the RAG_Retriever returns no results meeting the configured similarity threshold for a customer question, THEN THE Agent SHALL respond with a single clarifying question, SHALL NOT attempt to answer from general LLM knowledge, and SHALL NOT fabricate product attributes.
6. IF the RAG_Retriever returns no results meeting the configured similarity threshold on two consecutive customer questions in the same `Conversation`, THEN THE Agent SHALL set the Escalation_Flag on the `Conversation` and inform the customer that a human will follow up.
7. WHEN the Agent answers a customer question that involves a specific product attribute (price, stock, or variant availability), THE Agent SHALL call the corresponding Catalog_Service `Tool` with a request timeout of 5 seconds to retrieve the current value rather than relying on RAG snippets alone.
8. THE Catalog_Service SHALL provide a `search_products` tool that accepts a query string of 1 to 200 characters and optional filters (price range with non-negative bounds where minimum is less than or equal to maximum, category) and returns within 2 seconds at most 20 matching `Product` records with current price and stock per variant.
9. IF the `search_products` tool cannot retrieve both current price and stock for a candidate `Product_Variant`, THEN THE Catalog_Service SHALL exclude that variant from the result set rather than returning a partial record.
10. IF a Catalog_Service tool call fails, returns an error response, or exceeds the 5-second request timeout, THEN THE Agent SHALL reply that the requested information is temporarily unavailable, SHALL NOT answer the attribute question from RAG snippets, and SHALL offer to retry or escalate.
11. IF Catalog_Service tool calls fail or time out on two consecutive tool invocations in the same `Conversation`, THEN THE Agent SHALL set the Escalation_Flag on the `Conversation`.

### Requirement 4: Product Recommendation

**User Story:** As a customer, I want the agent to recommend products that match my needs, budget, and preferences, so that I can decide what to buy faster.

#### Acceptance Criteria

1. THE Agent SHALL produce product recommendations only from the result set returned by the Catalog_Service `search_products` tool or the RAG_Retriever, and SHALL NOT include any product, variant, price, or attribute that is not present in that result set.
2. WHEN a customer expresses a budget constraint as a maximum price, a minimum price, or a price range, THE Agent SHALL pass that constraint as a price filter (in the customer's stated currency) to the `search_products` tool and SHALL only recommend products whose `current_price` returned by the tool falls within the stated bounds inclusive.
3. WHEN a customer expresses one or more variant constraints (such as color, size, or other catalog-defined variant attributes), THE Agent SHALL pass each constraint to the Catalog_Service and SHALL only recommend `Product_Variant` records where every stated constraint exactly matches the corresponding attribute value returned by the Catalog_Service.
4. WHEN the Catalog_Service returns one or more matching products for the customer's stated constraints, THE Agent SHALL recommend at most 5 products in a single reply, ordered by the Catalog_Service relevance ranking.
5. IF the Catalog_Service returns zero matching products for the customer's stated constraints, THEN THE Agent SHALL reply with a message indicating that no matches were found and SHALL offer the customer the choice to either relax one or more named constraints or be escalated to a human agent.
6. IF a call to the Catalog_Service `search_products` tool or the RAG_Retriever fails, times out after 10 seconds, or returns an error response, THEN THE Agent SHALL reply with a message indicating that recommendations are temporarily unavailable, SHALL NOT recommend any product in that turn, and SHALL offer to retry or escalate to a human agent.
7. WHEN the Agent includes a recommended product in a reply, THE Agent SHALL include for each recommended product the product name, the `current_price` with currency, and at least one identifying attribute (variant label or SKU) exactly as returned by the Catalog_Service.

### Requirement 5: Cart Management with Stock and Price Validation

**User Story:** As a customer, I want to add, update, and remove items in a cart through the WhatsApp conversation, so that I can prepare an order before checking out.

#### Acceptance Criteria

1. THE System SHALL maintain at most one active `Cart` per `Customer` at any time, where an active `Cart` is one whose status is `OPEN`, has not been converted into an `Order`, and has not expired.
2. THE Catalog_Service SHALL expose an `add_to_cart` tool that accepts `customer_id`, `product_id`, `variant_id`, and a `quantity` integer in the range 1 to 999 inclusive.
3. WHEN the `add_to_cart` tool is invoked, THE Catalog_Service SHALL verify in order that the `Customer` exists, the `Product` exists, the `Product_Variant` exists and belongs to the specified `Product`, the requested `quantity` is in the range 1 to 999, and the available stock for the variant is greater than or equal to the sum of the requested `quantity` and any quantity of the same `Product_Variant` already present in the active `Cart`, before mutating the `Cart`.
4. IF any validation in `add_to_cart`, `remove_from_cart`, or `update_cart_item_quantity` fails, THEN THE Catalog_Service SHALL return a structured error result identifying which validation failed using one of the named identifiers `customer_not_found`, `product_not_found`, `variant_not_found`, `variant_product_mismatch`, `invalid_quantity`, `insufficient_stock`, or `cart_item_not_found`, and SHALL leave the `Cart` and `Cart_Item` records unchanged from their state before the tool invocation.
5. WHEN a `Cart_Item` is added or its quantity is updated, THE Catalog_Service SHALL persist the unit price snapshot on the `Cart_Item` taken from the current `Product_Variant` price at the time of the operation, and that snapshot SHALL remain unchanged when the live `Product_Variant` price changes later.
6. THE Catalog_Service SHALL expose a `remove_from_cart` tool that accepts `customer_id` and `cart_item_id` and an `update_cart_item_quantity` tool that accepts `customer_id`, `cart_item_id`, and a `quantity` integer in the range 1 to 999 inclusive, and both tools SHALL verify that the `Cart_Item` exists and belongs to the active `Cart` of the specified `Customer` before mutating it.
7. WHEN any cart-mutating tool is invoked successfully, THE Catalog_Service SHALL recompute the cart subtotal as the sum of `quantity * unit_price_snapshot` across all persisted `Cart_Item` rows of the active `Cart`, persist the new subtotal on the `Cart`, and SHALL NOT trust totals computed by the Agent.
8. IF the cart subtotal recomputation or persistence fails during a cart-mutating tool invocation, THEN THE Catalog_Service SHALL roll back the cart mutation transactionally, return a structured error, and SHALL NOT persist the cart change.
9. WHEN the Agent communicates cart contents or totals to the customer, THE Agent SHALL use the values returned by the most recent Catalog_Service tool response and SHALL NOT compute or restate cart contents or totals on its own.
10. WHEN any cart-mutating tool is invoked for a `Customer` that has no active `Cart`, THE Catalog_Service SHALL create a new active `Cart` with status `OPEN` for that `Customer` before applying the requested mutation.

### Requirement 6: Deterministic Order Creation with Customer Confirmation

**User Story:** As an online seller, I want orders to be created only after the customer explicitly confirms, with totals calculated by the backend, so that no order is placed by mistake or with an LLM-fabricated total.

#### Acceptance Criteria

1. THE Order_Service SHALL expose a `create_order` tool that accepts a `customer_id` (1 to 64 characters), a `cart_id` (1 to 64 characters), and a `customer_confirmation_token` (1 to 128 characters), and SHALL reject any other input shape with a validation error before any state read or mutation.
2. THE Agent SHALL request explicit customer confirmation by presenting the customer with the cart contents, the backend-computed total, and the currency returned by the Catalog_Service, and SHALL only invoke `create_order` after the customer responds with an affirmative confirmation message and SHALL pass a `customer_confirmation_token` derived deterministically from the cart snapshot (cart id, all cart item ids, quantities, unit price snapshots, and currency) at the moment of confirmation.
3. IF `create_order` is invoked with a `customer_confirmation_token` that does not match the deterministic token derived from the current `Cart` state, THEN THE Order_Service SHALL reject the request with a structured error containing a machine-readable error code, SHALL leave the `Cart` and all `Cart_Item` records unchanged, and SHALL NOT create an `Order`.
4. WHEN `create_order` is invoked with a valid confirmation token, THE Order_Service SHALL re-validate stock availability for every `Cart_Item` against the Catalog_Service within a 5-second total budget before persisting the `Order`.
5. IF stock re-validation fails for any `Cart_Item` during `create_order`, THEN THE Order_Service SHALL return a structured error identifying the affected `Cart_Item` with error code `insufficient_stock`, SHALL leave the `Cart` unchanged, and SHALL NOT create an `Order`.
6. IF current price or currency for any `Cart_Item` returned by the Catalog_Service during re-validation differs from the unit price snapshot or currency persisted on the `Cart_Item`, THEN THE Order_Service SHALL return a structured error identifying the affected `Cart_Item` with error code `price_changed`, SHALL invalidate the `customer_confirmation_token` for that `Cart`, SHALL leave the `Cart` unchanged, and SHALL NOT create an `Order`.
7. WHEN an `Order` is created, THE Order_Service SHALL compute the order total as the sum of `quantity * unit_price_snapshot` across all line items plus any backend-computed fees, persist this total in ISO 4217 currency and a UTC creation timestamp, and SHALL NOT accept any monetary value supplied by the Agent.
8. WHEN an `Order` is created, THE Order_Service SHALL set the initial `Order` status to `pending_payment` and SHALL associate the `Order` with the originating `cart_id` and `customer_id`.
9. THE Agent SHALL communicate order totals, currencies, and order IDs to the customer using only the values returned by the `create_order` tool response and SHALL NOT compute, format, or substitute these values.

### Requirement 7: Payment Link Generation via Sandbox Provider

**User Story:** As a customer, I want to receive a payment link after confirming my order, so that I can complete payment outside of WhatsApp.

#### Acceptance Criteria

1. THE Payment_Service SHALL expose a `create_payment_link` tool that accepts an `order_id` string of 1 to 64 characters and SHALL reject any other input shape with a validation error before contacting the Payment_Provider.
2. WHEN `create_payment_link` is invoked for an existing `Order` whose status is `pending_payment`, THE Payment_Service SHALL request a payment link from the configured Payment_Provider with a request timeout of 10 seconds and, upon a successful provider response, SHALL persist a `Payment` record containing the `order_id`, status `created`, the provider reference, the issued link URL, the creation timestamp, and an expiry timestamp before returning the link to the caller.
3. IF `create_payment_link` is invoked with an `order_id` that does not exist or whose `Order` status is not `pending_payment`, THEN THE Payment_Service SHALL return a structured error response containing a machine-readable error code and a human-readable message identifying the rejection reason, SHALL NOT request a new payment link from the Payment_Provider, and SHALL leave all existing `Order` and `Payment` records unchanged.
4. IF a `Payment` record with status `paid`, or with status `created` and an expiry timestamp in the future, already exists for an `Order`, THEN THE Payment_Service SHALL return the existing payment link together with its current status and expiry timestamp and SHALL NOT issue a new request to the Payment_Provider.
5. IF the Payment_Provider request fails because the 10-second timeout is exceeded, the network call errors, or the provider returns a non-success response, THEN THE Payment_Service SHALL return a structured error response indicating provider unavailability, SHALL NOT persist a `Payment` record with status `created`, and SHALL leave the `Order` status as `pending_payment`.
6. WHEN the Agent receives a successful `create_payment_link` tool response, THE Agent SHALL deliver to the customer only the URL string returned by the tool and SHALL NOT alter, shorten, wrap, or substitute that URL.
7. THE System SHALL load the configured Payment_Provider name, sandbox base URL, and credentials from environment variables at startup and SHALL NOT contain any of these values as hardcoded literals in source files.

### Requirement 8: Verified and Idempotent Payment Webhook Handling

**User Story:** As an online seller, I want payment status to update only from verified provider callbacks and to be safe against duplicate deliveries, so that orders are never marked paid incorrectly or processed twice.

#### Acceptance Criteria

1. THE API_Service SHALL expose a `POST /webhooks/payment` endpoint that accepts Payment_Provider callbacks with `Content-Type: application/json` and a request body size of up to 256 KB.
2. WHEN a `POST /webhooks/payment` request is received, THE Payment_Webhook_Verifier SHALL validate the request signature using the configured `PAYMENT_WEBHOOK_SECRET` within 500 milliseconds before any state mutation.
3. IF the payment webhook signature header is missing, malformed, or does not match the computed signature, THEN THE API_Service SHALL respond within 1 second with HTTP status 401 and SHALL NOT modify any `Payment` or `Order` records and SHALL NOT persist a `webhook_event_id`.
4. THE Payment_Service SHALL persist a unique `webhook_event_id` for every payment webhook that is both signature-verified and successfully processed, atomically with any associated `Payment` or `Order` state changes.
5. IF an incoming verified payment webhook has a `webhook_event_id` that already exists in the persisted set, THEN THE Payment_Service SHALL respond within 1 second with HTTP status 200, SHALL NOT modify any `Payment` or `Order` state, and SHALL NOT enqueue any logistics preparation task or customer notification.
6. WHEN a verified payment webhook indicates a successful payment for a `Payment` whose status is `created`, THE Payment_Service SHALL update the `Payment` status to `paid`, update the associated `Order` status to `paid`, persist the verification metadata (provider reference, provider event id, payment timestamp, raw payload), and persist the `webhook_event_id` in a single database transaction within 5 seconds.
7. IF a verified payment webhook indicates a successful payment for a `Payment` whose status is already `paid`, THEN THE Payment_Service SHALL persist the `webhook_event_id`, respond within 1 second with HTTP status 200, and SHALL NOT change the `Order` status, enqueue logistics preparation, or send any additional customer notification.
8. WHEN a verified payment webhook indicates a failed payment, THE Payment_Service SHALL update the `Payment` status to `failed`, leave the associated `Order` status unchanged at `pending_payment`, and persist the `webhook_event_id`, all within a single database transaction.
9. WHEN a verified payment webhook indicates an expired payment, THE Payment_Service SHALL update the `Payment` status to `expired`, leave the associated `Order` status unchanged at `pending_payment`, and persist the `webhook_event_id`, all within a single database transaction.
10. IF a verified payment webhook references a `payment_id` or provider reference that does not match any persisted `Payment` record, THEN THE Payment_Service SHALL respond within 1 second with HTTP status 200, log the unmatched event in the Audit_Logger with the provider reference, and SHALL NOT modify any `Payment` or `Order` records.
11. IF a payment webhook payload is unparseable or fails schema validation after signature verification, THEN THE Payment_Service SHALL respond within 1 second with HTTP status 400, log the parse failure in the Audit_Logger, and SHALL NOT persist a `webhook_event_id` or modify any `Payment` or `Order` records.
12. WHEN an `Order` transitions to `paid` as a result of a verified payment webhook, THE System SHALL enqueue exactly one logistics preparation task and exactly one customer payment-confirmation reply, deduplicated by `order_id` so that any subsequent attempt to enqueue for the same `order_id` is rejected.

### Requirement 9: Simulated Logistics Preparation After Verified Payment

**User Story:** As a customer, I want to receive shipping confirmation and a tracking reference after my payment succeeds, so that I know my order is being prepared.

#### Acceptance Criteria

1. THE Logistics_Service SHALL expose a `prepare_shipment` operation that accepts an `order_id` of 1 to 64 characters.
2. WHEN `prepare_shipment` is invoked for an existing `Order` whose status is `paid`, THE Logistics_Service SHALL create a simulated shipment record containing a unique tracking number of 8 to 32 alphanumeric characters, set the `Order` status to `shipment_prepared`, and persist the shipment record and the updated order status atomically within 5 seconds.
3. IF `prepare_shipment` is invoked for an `order_id` that does not correspond to an existing `Order`, THEN THE Logistics_Service SHALL return a structured error indicating the order was not found and SHALL NOT create a shipment record.
4. IF `prepare_shipment` is invoked for an `Order` whose status is neither `paid` nor `shipment_prepared`, THEN THE Logistics_Service SHALL return a structured error indicating the order status is ineligible for shipment preparation and SHALL NOT create a shipment record.
5. IF `prepare_shipment` is invoked for an `Order` that already has a shipment record, THEN THE Logistics_Service SHALL return the existing shipment record, SHALL NOT create a duplicate shipment, and the System SHALL NOT send an additional WhatsApp tracking notification for that shipment.
6. WHEN a shipment is newly created by `prepare_shipment`, THE System SHALL send exactly one WhatsApp notification to the `Customer` containing the tracking number within 30 seconds, using values returned by the Logistics_Service.

### Requirement 10: Human Escalation with Preserved Context

**User Story:** As an operations admin, I want the agent to flag conversations for human handoff and stop replying automatically, with full context preserved, so that I can take over without losing information.

#### Acceptance Criteria

1. WHEN the customer message classified by the Agent indicates a request for human support, a refund or cancellation request, or a payment problem report, THE Agent SHALL set the Escalation_Flag on the `Conversation`.
2. IF the Agent's confidence score for the candidate reply falls below the configured threshold `ESCALATION_CONFIDENCE_THRESHOLD` (a decimal value in the range 0.0 to 1.0 inclusive), THEN THE Agent SHALL set the Escalation_Flag on the `Conversation` and SHALL suppress sending the candidate reply.
3. WHILE the Escalation_Flag is set on a `Conversation`, THE Agent SHALL NOT generate any further automated replies for that `Conversation`.
4. WHILE the Escalation_Flag is set on a `Conversation`, THE System SHALL persist every inbound message in a queue for human review and SHALL NOT auto-respond to those messages.
5. WHEN the Escalation_Flag is set, THE System SHALL persist within 5 seconds the full Conversation_State, at least the most recent 50 messages, and any associated `Cart` and `Order` references so a human admin can resume context.
6. IF persisting the escalation context fails after the Escalation_Flag has been set, THEN THE System SHALL retry the persistence up to 3 times with at least 1-second backoff, leave the Escalation_Flag set, log to the Audit_Logger a record containing the conversation id, the persistence failure reason, and the attempt count, and SHALL NOT roll back the escalation.
7. THE System SHALL expose an internal admin endpoint `POST /admin/conversations/{conversation_id}/resume` that clears the Escalation_Flag for a `Conversation` only when the request is authenticated and authorized as an admin.
8. IF a request to `POST /admin/conversations/{conversation_id}/resume` is unauthenticated or not authorized as an admin, THEN THE API_Service SHALL respond with HTTP status 401 or 403, SHALL leave the Escalation_Flag unchanged, and SHALL log the rejected attempt in the Audit_Logger.
9. WHEN the Escalation_Flag is cleared via the admin endpoint, THE System SHALL record in the Audit_Logger the admin identity, the conversation id, and the action timestamp in ISO 8601 UTC format.

### Requirement 11: Audit Logging of Important Tool Calls and Decisions

**User Story:** As an operations admin, I want every important agent tool call and state-changing action to be auditable, so that I can investigate issues and verify system behavior.

#### Acceptance Criteria

1. WHEN any of the tools `search_products`, `add_to_cart`, `remove_from_cart`, `update_cart_item_quantity`, `create_order`, `create_payment_link`, or `prepare_shipment` is invoked, THE Audit_Logger SHALL persist exactly one structured record for that invocation indicating success or error outcome.
2. THE Audit_Logger SHALL include in each tool-invocation record the tool name, input parameters, output result on success or error code on failure, ISO 8601 UTC timestamp, `conversation_id`, `customer_id`, and `order_id` (with the value `null` when not applicable).
3. IF a payment webhook or WhatsApp webhook fails signature verification, THEN THE Audit_Logger SHALL persist a record containing the source IP, requested endpoint, ISO 8601 UTC timestamp, and an enumerated verification failure reason from the set `signature_missing`, `signature_malformed`, or `signature_mismatch`.
4. WHEN the Escalation_Flag is set or cleared, THE Audit_Logger SHALL persist a record identifying the trigger reason and the actor (Agent identifier or admin user identifier).
5. THE Audit_Logger SHALL store records in PostgreSQL.
6. THE Audit_Logger MAY persist records asynchronously, but SHALL NOT block the synchronous user-facing response path by more than 100 milliseconds, and every targeted action SHALL produce exactly one corresponding audit record (deduplicated by tool invocation id or webhook event id).
7. IF an audit record write fails or its asynchronous queue is unavailable, THEN THE Audit_Logger SHALL retry the write up to 3 times with at least 1-second backoff and SHALL place permanently failing records in a durable failure queue for later retry, so no targeted action loses its audit record.
8. THE Audit_Logger SHALL redact credentials, API keys, secrets, and full payment account numbers from any logged input or output before persistence.

### Requirement 12: Configurable LLM Provider and Cloud Run Deployment Readiness

**User Story:** As a developer, I want the LLM provider and runtime configuration to be controlled by environment variables and the application to be deployable to Cloud Run, so that I can switch providers and ship the service without code changes.

#### Acceptance Criteria

1. WHEN the application starts, THE LLM_Factory SHALL select the chat model implementation based on the `LLM_PROVIDER` environment variable, supporting exactly the case-insensitive values `openai`, `anthropic`, and `google`.
2. WHEN the LLM_Factory instantiates a chat model, THE LLM_Factory SHALL read the model identifier from the `LLM_MODEL` environment variable (maximum 200 characters, non-empty after trimming whitespace) and pass it unchanged to the selected provider client.
3. IF `LLM_PROVIDER` is unset, empty, or set to a value not in the supported set (`openai`, `anthropic`, `google`), THEN THE System SHALL terminate startup within 5 seconds, emit a startup error log identifying the offending variable and the list of supported values, and SHALL NOT bind the HTTP listener or accept any requests.
4. IF `LLM_MODEL` is unset or empty, or if the credential environment variable required by the selected provider (`OPENAI_API_KEY` for `openai`, `ANTHROPIC_API_KEY` for `anthropic`, `GOOGLE_API_KEY` for `google`) is unset or empty, THEN THE System SHALL terminate startup within 5 seconds, emit a startup error log identifying the missing variable, and SHALL NOT begin serving requests.
5. THE Agent SHALL obtain its chat model exclusively through the LLM_Factory and SHALL NOT import or instantiate provider-specific clients directly inside any agent graph module.
6. WHEN a `GET /healthz` request is received, THE API_Service SHALL respond within 1 second with HTTP status 200 and a JSON body indicating liveness, without performing any database or external dependency calls.
7. WHEN a `GET /readyz` request is received, THE API_Service SHALL respond within 5 seconds with HTTP status 200 and a JSON body indicating readiness only if the database connection check and the Checkpointer storage reachability check both succeed within that window.
8. IF the database connection check or the Checkpointer storage reachability check fails or exceeds 5 seconds during a `GET /readyz` request, THEN THE API_Service SHALL respond with HTTP status 503 and a JSON body indicating which dependency is unavailable.
9. WHEN the application starts, THE API_Service SHALL bind to the TCP port specified by the `PORT` environment variable, defaulting to 8080 if `PORT` is unset, accepting integer values in the range 1 to 65535.
10. IF `PORT` is set to a non-integer value or to an integer outside the range 1 to 65535, THEN THE System SHALL terminate startup within 5 seconds, emit a startup error log identifying the invalid `PORT` value, and SHALL NOT begin serving requests.
11. THE System SHALL load all secrets and provider credentials exclusively from environment variables at runtime and SHALL NOT contain hardcoded secret values, API keys, or credential strings in any tracked source file.
12. THE System SHALL include a `Dockerfile` at the repository root that produces a runnable container image exposing the configured `PORT` and starting the API_Service as its default process.
13. THE System SHALL include a `docker-compose.yml` at the repository root that runs the API_Service container together with a PostgreSQL instance with the pgvector extension enabled, wiring the API_Service to the database via environment variables for local development.

### Requirement 13: Local Development Setup with uv

**User Story:** As a developer, I want a reproducible local development setup using `uv`, so that I can install dependencies, run migrations, and start the service quickly.

#### Acceptance Criteria

1. THE System SHALL declare its Python dependencies in a `pyproject.toml` file at the repository root and SHALL pin transitive dependencies in a `uv.lock` file at the repository root.
2. THE System SHALL declare a Python interpreter requirement of `>=3.11` in `pyproject.toml`.
3. WHEN a developer runs `uv sync` from the repository root on a network connection of at least 10 Mbps, THE System SHALL install all required runtime and development dependencies into a `uv`-managed virtual environment using the locked versions and SHALL complete with exit code 0 within 600 seconds.
4. IF `uv sync` fails because of a network error, missing Python interpreter satisfying `>=3.11`, or a `uv.lock` mismatch with `pyproject.toml`, THEN `uv sync` SHALL exit with a non-zero status code and SHALL emit an error message identifying the failure cause.
5. THE System SHALL provide a `.env.example` file at the repository root that lists every environment variable the application reads (including at minimum `LLM_PROVIDER`, `LLM_MODEL`, `DATABASE_URL`, `PORT`, `WHATSAPP_GATEWAY_URL`, `WHATSAPP_GATEWAY_INTERNAL_TOKEN`, `WHATSAPP_GATEWAY_AUTH_DIR`, `WHATSAPP_BACKEND_INBOUND_URL`, `PAYMENT_WEBHOOK_SECRET`, and the per-provider credential variables) with non-empty placeholder values, and SHALL NOT contain any credential, token, key, or connection string granting access to a real external service.
6. THE System SHALL provide a script under `scripts/` that runs database migrations against the configured `DATABASE_URL` and seeds at least 5 product catalog records and at least 5 FAQ records for local development.
7. WHEN the migration and seed script is run more than once against the same `DATABASE_URL`, THE script SHALL be idempotent and SHALL NOT duplicate seeded records.
8. IF the migration and seed script cannot connect to the configured `DATABASE_URL` or fails at any migration or seed step, THEN the script SHALL exit with a non-zero status code and SHALL emit an error message identifying the failing step.
9. WHEN a developer runs `uv run uvicorn app.main:app --reload --port 8080` from the repository root with every environment variable listed in `.env.example` set to a non-empty valid value, THE API_Service SHALL be ready to accept requests within 30 seconds of process start.
10. WHEN the API_Service is running locally, THE API_Service SHALL respond to `GET /healthz` with HTTP status 200 within 1000 milliseconds.
11. THE System SHALL include a `README.md` at the repository root that documents (a) prerequisites including required `uv` and Python versions, (b) the `uv sync` setup command, (c) environment variable configuration via `.env.example`, (d) the migration and seed script execution, and (e) the local run command.
