---
inclusion: always
---

# Product Overview

## Product Name

WhatsApp Sales Agent

## Product Summary

WhatsApp Sales Agent is an AI-powered conversational commerce assistant for online sellers. It acts as a customer service and sales agent inside WhatsApp, helping customers discover products, ask product-related questions, build an order, receive a payment link, and get shipping updates after payment is completed.

The product is designed for small to medium online sellers who want to provide fast, consistent, and scalable customer support without manually replying to every WhatsApp message.

## Core Problem

Many online sellers rely on WhatsApp as their main sales and customer service channel. As message volume grows, sellers struggle to:

- Respond quickly to product questions
- Recommend the right products
- Track customer intent and conversation history
- Convert interested leads into paid orders
- Manage payment confirmation reliably
- Prepare shipment after successful payment
- Avoid losing prospects due to slow response times

This product solves that by automating the sales conversation while still keeping critical business actions such as pricing, stock validation, payment status, and shipment creation handled by deterministic backend services.

## Target Users

### Primary Users

Online sellers, merchants, and small business owners who sell through WhatsApp and need automated customer service and sales support.

### Secondary Users

Operations/admin teams who need to monitor conversations, review orders, handle escalations, and manage fulfillment.

### End Customers

Customers who contact the seller through WhatsApp to ask about products, place orders, pay, and track shipping.

## Product Goals

- Automate common WhatsApp sales and customer service conversations
- Answer product questions using trusted product catalog and FAQ data
- Recommend products based on customer needs and preferences
- Help customers build carts and proceed to checkout
- Generate third-party payment links for valid orders
- Detect payment completion through payment provider callbacks
- Trigger logistics preparation after successful payment
- Track conversation state across multi-turn WhatsApp interactions
- Escalate sensitive, ambiguous, or high-risk cases to a human admin
- Maintain audit logs for important agent decisions and tool calls

## Key Features

### 1. WhatsApp Conversation Handling

The system receives customer messages through WhatsApp webhooks and sends replies back through the WhatsApp Business API.

The agent should support natural multi-turn conversations such as:

- Product inquiry
- Product recommendation
- Price inquiry
- Stock inquiry
- Cart update
- Checkout request
- Payment status question
- Shipping status question
- Complaint or support request

### 2. Product Question Answering

The agent answers customer questions using product catalog data and product knowledge documents.

The agent may answer questions about:

- Product benefits
- Product variants
- Product availability
- Product usage
- Product comparison
- Size, color, or specification details
- Return, warranty, or policy information when available

The agent must not invent product information. If product data is unavailable, it should ask a clarifying question or escalate to a human.

### 3. Product Recommendation

The agent can recommend products based on customer intent, budget, use case, preference, or constraints.

Example customer messages:

- "Ada moisturizer buat kulit berminyak?"
- "Yang cocok untuk hadiah apa?"
- "Mau yang di bawah 100 ribu"
- "Ada warna hitam ukuran M?"

Recommendations must be grounded in available product data.

### 4. Cart and Order Management

The agent helps customers add, remove, or update items in their cart.

The backend must validate:

- Product exists
- Variant exists
- Stock is available
- Quantity is valid
- Price is correct
- Order total is calculated by the system, not by the LLM

The agent may guide the conversation, but order creation must be performed by deterministic order service logic.

### 5. Payment Link Generation

After cart validation and customer confirmation, the system creates an order and generates a payment link through a third-party payment provider.

The agent should send the payment link to the customer and explain the next step clearly.

The agent must never mark an order as paid based only on customer claims. Payment status must come from the payment service or verified payment webhook.

### 6. Payment Callback Handling

The system receives payment provider webhook callbacks and updates order status after verifying the callback.

When payment succeeds, the system should:

- Mark payment as paid
- Mark order as ready for fulfillment
- Notify the customer through WhatsApp
- Trigger logistics preparation

Payment processing must be idempotent to avoid duplicate fulfillment.

### 7. Logistics Preparation

After successful payment, the system prepares shipping.

Depending on integration maturity, logistics may be:

- Dummy/simulated for MVP
- Integrated with a shipping aggregator
- Integrated directly with courier APIs

The system should support:

- Shipping rate lookup
- Shipment creation
- Tracking number retrieval
- Shipment status updates
- Customer shipping notifications

### 8. Human Escalation

The agent should escalate to a human admin when:

- Customer asks for refund or cancellation
- Customer reports payment problems
- Customer complains aggressively or emotionally
- Product data is missing or ambiguous
- Address validation fails
- Order state is inconsistent
- The customer requests human support
- The agent confidence is low

Escalation should preserve conversation context so the human admin can continue smoothly.

## Agent Responsibilities

The AI agent is responsible for:

- Understanding customer intent
- Asking clarifying questions
- Retrieving relevant product information
- Explaining products in a friendly sales/customer-service tone
- Calling backend tools for product search, cart updates, order creation, payment link creation, and shipment status
- Maintaining conversation state
- Following business rules and safety constraints
- Escalating to human support when needed

## Backend Responsibilities

Backend services are responsible for:

- Product catalog truth
- Stock validation
- Price calculation
- Cart persistence
- Order creation
- Payment provider integration
- Payment webhook verification
- Logistics provider integration
- Shipment creation
- Audit logging
- Idempotency
- Authentication and authorization
- Data persistence

## Important Product Principles

### 1. Deterministic Systems Own Transactions

The LLM must not be the source of truth for prices, stock, payment status, order status, or shipment status.

All transactional actions must be confirmed through backend tools or service responses.

### 2. Conversation Should Be Helpful but Safe

The agent should be friendly, concise, and helpful, but it must avoid overpromising.

The agent should say when it does not know something and should escalate when needed.

### 3. State Must Persist Across Messages

WhatsApp conversations are asynchronous and multi-turn. The system must persist conversation state, cart state, order state, and relevant history.

### 4. Every Important Action Should Be Auditable

Tool calls that affect business state should be logged, including input, output, timestamp, customer id, and order id when available.

### 5. Human Handoff Is Part of the Product

The goal is not to eliminate humans completely. The goal is to automate repetitive work and route complex cases to humans with better context.

## Example Customer Journey

1. Customer sends a WhatsApp message asking about a product.
2. Agent identifies the intent and retrieves product information.
3. Agent answers the question and recommends suitable products.
4. Customer chooses a product and variant.
5. Agent calls cart/order tools to validate stock and add item to cart.
6. Agent asks for required checkout details.
7. Backend calculates total price and shipping options.
8. Customer confirms the order.
9. Backend creates order and payment link.
10. Agent sends payment link to customer.
11. Payment provider sends verified payment webhook.
12. Backend marks order as paid.
13. Logistics process is triggered.
14. Customer receives confirmation and tracking information.
15. Human support is involved only if needed.

## MVP Scope

The first version should focus on:

- WhatsApp webhook message receiving
- WhatsApp reply sending
- Product catalog search
- Product Q&A using catalog and FAQ data
- Simple cart management
- Order creation
- Dummy or sandbox payment link generation
- Simulated payment webhook
- Simulated logistics/tracking
- LangGraph-based conversation state
- Basic human escalation flag
- Conversation and tool audit logging

## Out of Scope for MVP

The MVP should not include:

- Multi-merchant marketplace support
- Complex promotion engine
- Real-time warehouse management
- Advanced CRM segmentation
- Full admin dashboard
- Refund automation
- Subscription billing
- Multi-language support unless explicitly added later
- Real logistics provider integration unless MVP is already stable
- Manual bank transfer reconciliation without human review

## Success Metrics

The product should be evaluated using:

- Average first response time
- Percentage of product questions answered automatically
- Lead-to-checkout conversion rate
- Checkout-to-payment conversion rate
- Percentage of conversations requiring human handoff
- Number of failed or inconsistent order states
- Payment callback processing accuracy
- Customer satisfaction from WhatsApp conversations
- Reduction in manual seller response workload

## Product Constraints

- WhatsApp is the primary user interface.
- The agent must support asynchronous customer conversations.
- The system must be safe for commerce workflows.
- Payment status must only come from verified payment provider data.
- Shipping must only be prepared after verified payment success.
- Product answers must be grounded in seller-provided product data.
- The system should be designed for deployment on Cloud Run.
- The architecture should support future separation into MCP servers for catalog, order, payment, and logistics tools.

## Intended Architecture Direction

The product should be implemented as an event-driven AI commerce system.

Recommended high-level architecture:

- WhatsApp webhook service receives incoming messages.
- Message events are persisted and queued.
- LangGraph orchestrates the conversation flow.
- LangChain provides model, prompt, retriever, and tool abstractions.
- MCP tools expose catalog, order, payment, and logistics capabilities.
- Backend services handle deterministic business logic.
- Cloud SQL or PostgreSQL stores products, customers, carts, orders, payments, shipments, conversation logs, and agent checkpoints.
- Cloud Tasks or Pub/Sub handles asynchronous processing.
- Secret Manager stores API keys and provider credentials.
- Cloud Run hosts API, worker, and optional MCP services.

## Future Enhancements

Potential future versions may include:

- Admin dashboard for merchants
- Multi-store support
- Real payment gateway integration
- Real logistics provider integration
- Customer segmentation
- Broadcast campaign support
- Abandoned cart follow-up
- Product analytics
- Sales performance dashboard
- Human support inbox
- Voice note understanding
- Image-based product inquiry
- Multi-language conversations
- Loyalty and voucher support

## Agent Behavior Guidelines

The agent should:

- Use a warm, helpful, sales-oriented tone
- Keep WhatsApp replies concise and easy to read
- Ask one or two questions at a time
- Confirm important customer choices before checkout
- Avoid making unsupported claims
- Avoid inventing discounts, stock, delivery estimates, or policies
- Use tool results as the source of truth
- Escalate when confidence is low or risk is high
- Prioritize customer clarity over aggressive selling

## Non-Negotiable Rules

- Never create an order without customer confirmation.
- Never mark payment as paid without verified payment provider confirmation.
- Never prepare shipment before successful payment.
- Never invent product details, stock, price, discount, warranty, or shipping promises.
- Never expose internal tool errors directly to customers.
- Never store sensitive credentials in code or steering files.
- Always log important tool calls.
- Always preserve enough conversation context for human handoff.