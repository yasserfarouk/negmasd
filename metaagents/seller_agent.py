"""
Seller Agent with MQTT Integration for Negotiation
Based on the negmasd architecture with on_create, on_receive, on_destroy pattern
"""

import json
import paho.mqtt.client as mqtt
from typing import Dict, Any, List
import threading
import time

# Global state
config = {}
mqtt_client = None
agent_topic = "seller"
response_topic = "seller_response"
negotiations = {}

def on_create(data: dict) -> dict | None:
    """
    Initialize the seller agent with configuration.
    
    Args:
        data (dict): Configuration data including:
            - mqtt_broker: MQTT broker address
            - mqtt_port: MQTT broker port
            - negotiation_config: Seller negotiation parameters
            
    Returns:
        dict | None: Initialization status
    """
    global config, mqtt_client
    
    config = data.copy()
    config.setdefault("mqtt_broker", "localhost")
    config.setdefault("mqtt_port", 1883)
    config.setdefault("name", "seller")
    config.setdefault("negotiation_config", {
        "min_price": 150,
        "max_quantity": 8,
        "min_quantity": 1,
        "reserved_value": 0.2,
        "strategy": "BoulwareTBNegotiator"
    })
    
    # Initialize MQTT client
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    
    try:
        mqtt_client.connect(config["mqtt_broker"], config["mqtt_port"], 60)
        mqtt_client.loop_start()
        return {"status": "seller_agent_initialized", "config": list(config.keys())}
    except Exception as e:
        return {"error": f"Failed to initialize MQTT: {str(e)}"}


def on_mqtt_connect(client, userdata, flags, rc):
    """Callback for when MQTT client connects"""
    if rc == 0:
        print(f"Seller agent connected to MQTT broker")
        client.subscribe(agent_topic)
    else:
        print(f"Failed to connect to MQTT broker: {rc}")


def on_mqtt_message(client, userdata, msg):
    """Handle incoming MQTT messages and route to agent processing"""
    try:
        message_data = json.loads(msg.payload.decode())
        
        # Process message through agent logic
        response = on_receive([message_data])
        
        # Send response back via MQTT to specified topic
        if response and "result" in response and "topic" in response:
            client.publish(response["topic"], response["result"])
            
    except Exception as e:
        error_response = {"error": f"Message processing failed: {str(e)}"}
        client.publish(response_topic, json.dumps(error_response))


def on_receive(data: list) -> dict:
    """
    Main processing entry point for seller agent negotiations.
    
    Args:
        data (list): List of negotiation messages/offers
                    
    Returns:
        dict: Seller's response with decisions and counter-offers, including target topic
    """
    if not data:
        return {"result": json.dumps({"error": "No data provided"}), "agent": "seller", "topic": "mechanism"}
    
    message = data[0]
    message_type = message.get("type", "unknown")
    session_id = message.get("session_id", "default")
    from_agent = message.get("from_agent", "mechanism")
    
    response = {
        "agent": "seller",
        "session_id": session_id,
        "timestamp": time.time()
    }
    
    # Determine response topic based on message type and sender
    if message_type == "start_negotiation":
        response.update(handle_negotiation_start(message))
        target_topic = "mechanism"  # Respond to mechanism for coordination
    elif message_type == "offer":
        response.update(handle_offer(message))
        if from_agent == "buyer":
            target_topic = "mechanism"  # Route through mechanism
        else:
            target_topic = "mechanism"  # Default to mechanism
    elif message_type == "accept":
        response.update(handle_acceptance(message))
        target_topic = "mechanism"
    elif message_type == "reject":
        response.update(handle_rejection(message))
        target_topic = "mechanism"
    elif message_type == "end_negotiation":
        response.update(handle_negotiation_end(message))
        target_topic = "mechanism"
    else:
        response.update({"error": f"Unknown message type: {message_type}"})
        target_topic = "mechanism"
    
    return {"result": json.dumps(response), "agent": "seller", "topic": target_topic}


def handle_negotiation_start(message: dict) -> dict:
    """Handle negotiation start request"""
    session_id = message.get("session_id")
    outcome_space = message.get("outcome_space", {})
    
    # Store negotiation session
    negotiations[session_id] = {
        "status": "active",
        "outcome_space": outcome_space,
        "offers_made": [],
        "offers_received": []
    }
    
    # Wait for buyer's initial offer (seller typically responds rather than initiates)
    return {
        "type": "ready",
        "message": "Seller ready to receive offers"
    }


def handle_offer(message: dict) -> dict:
    """Handle incoming offer from buyer"""
    session_id = message.get("session_id")
    offer = message.get("offer", {})
    
    if session_id not in negotiations:
        return {"error": "Unknown negotiation session"}
    
    # Record received offer
    negotiations[session_id]["offers_received"].append(offer)
    
    # Evaluate offer
    utility = calculate_seller_utility(offer)
    reserved_value = config["negotiation_config"]["reserved_value"]
    
    if utility >= reserved_value:
        # Accept offer
        negotiations[session_id]["status"] = "accepted"
        return {
            "type": "accept",
            "offer": offer,
            "utility": utility,
            "message": "Seller accepts the offer"
        }
    else:
        # Generate counter-offer
        counter_offer = generate_seller_offer(
            negotiations[session_id]["outcome_space"], 
            session_id, 
            previous_offer=offer
        )
        
        if counter_offer:
            return {
                "type": "offer",
                "offer": counter_offer,
                "message": "Seller counter-offer"
            }
        else:
            # Reject if no viable counter-offer
            negotiations[session_id]["status"] = "rejected"
            return {
                "type": "reject",
                "message": "No acceptable offer can be made"
            }


def handle_acceptance(message: dict) -> dict:
    """Handle offer acceptance from buyer"""
    session_id = message.get("session_id")
    
    if session_id in negotiations:
        negotiations[session_id]["status"] = "completed"
    
    return {
        "type": "negotiation_complete",
        "status": "success",
        "message": "Buyer accepted seller's offer"
    }


def handle_rejection(message: dict) -> dict:
    """Handle offer rejection from buyer"""
    session_id = message.get("session_id")
    
    if session_id in negotiations:
        negotiations[session_id]["status"] = "rejected"
    
    return {
        "type": "negotiation_complete",
        "status": "failed",
        "message": "Buyer rejected seller's offer"
    }


def handle_negotiation_end(message: dict) -> dict:
    """Handle negotiation termination"""
    session_id = message.get("session_id")
    
    if session_id in negotiations:
        negotiations[session_id]["status"] = "ended"
    
    return {
        "type": "negotiation_complete",
        "status": "terminated",
        "message": "Negotiation ended"
    }


def generate_seller_offer(outcome_space: dict, session_id: str, previous_offer: dict = None) -> dict:
    """Generate a seller offer based on preferences and strategy"""
    
    # Extract price and quantity ranges from outcome space
    price_values = outcome_space.get("issues", {}).get("price", {}).get("values", [100, 150, 200, 250, 300])
    quantity_range = outcome_space.get("issues", {}).get("quantity", {}).get("values", [1, 10])
    
    seller_config = config["negotiation_config"]
    min_price = seller_config["min_price"]
    max_quantity = seller_config["max_quantity"]
    
    if previous_offer:
        # Counter-offer: make concession from seller's perspective
        buyer_price = previous_offer.get("price", price_values[0])
        buyer_quantity = previous_offer.get("quantity", quantity_range[0])
        
        # Seller wants higher price, can be flexible on quantity
        available_prices = [p for p in price_values if p >= min_price]
        
        if buyer_price >= min_price:
            # Buyer's price is acceptable, might accept or ask for slight increase
            price = buyer_price
        else:
            # Find a price between buyer's offer and seller's minimum
            higher_prices = [p for p in available_prices if p > buyer_price]
            if higher_prices:
                price = higher_prices[0]  # Take the lowest acceptable price above buyer's offer
            else:
                return None  # No viable counter-offer
        
        # Be flexible on quantity
        quantity = min(max_quantity, buyer_quantity)
    else:
        # Initial offer (shouldn't happen often for seller)
        price = min([p for p in price_values if p >= min_price])
        quantity = max_quantity
    
    offer = {"price": price, "quantity": quantity}
    
    # Record the offer
    if session_id in negotiations:
        negotiations[session_id]["offers_made"].append(offer)
    
    return offer


def calculate_seller_utility(offer: dict) -> float:
    """Calculate utility of an offer from seller's perspective"""
    price = offer.get("price", 0)
    quantity = offer.get("quantity", 0)
    
    # Seller utility: higher price and reasonable quantity are better
    # Normalize to 0-1 scale
    price_values = [100, 150, 200, 250, 300]  # From config
    max_price = max(price_values)
    min_price = config["negotiation_config"]["min_price"]
    max_quantity = config["negotiation_config"]["max_quantity"]
    
    # Price utility increases with price
    price_utility = max(0, (price - min_price) / (max_price - min_price))
    
    # Quantity utility (seller prefers reasonable quantities)
    quantity_utility = min(1.0, quantity / max_quantity)
    
    # Weighted combination (price weight higher for seller)
    total_utility = 0.8 * price_utility + 0.2 * quantity_utility
    
    return total_utility


def on_destroy() -> dict | None:
    """
    Cleanup when agent is shutting down.
    
    Returns:
        dict | None: Final status and statistics
    """
    global mqtt_client
    
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    
    return {
        "agent": "seller",
        "final_status": "destroyed",
        "negotiations_handled": len(negotiations),
        "config_used": config
    }


# Test code
if __name__ == "__main__":
    # Test configuration
    test_config = {
        "mqtt_broker": "localhost", 
        "mqtt_port": 1883,
        "negotiation_config": {
            "min_price": 150,
            "max_quantity": 8,
            "min_quantity": 1,
            "reserved_value": 0.25
        }
    }
    
    on_create(test_config)
    
    # Test offer response
    offer_message = {
        "type": "offer",
        "session_id": "test_session_1",
        "offer": {"price": 120, "quantity": 5},
        "outcome_space": {
            "issues": {
                "price": {"values": [100, 150, 200, 250, 300]},
                "quantity": {"values": [1, 10]}
            }
        }
    }
    
    # First set up negotiation
    start_message = {
        "type": "start_negotiation",
        "session_id": "test_session_1",
        "outcome_space": {
            "issues": {
                "price": {"values": [100, 150, 200, 250, 300]},
                "quantity": {"values": [1, 10]}
            }
        }
    }
    
    on_receive([start_message])
    result = on_receive([offer_message])
    
    print("Seller Agent Test Result:")
    print(result["result"])
    
    on_destroy()