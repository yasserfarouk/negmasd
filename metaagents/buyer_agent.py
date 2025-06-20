"""
Buyer Agent with MQTT Integration for Negotiation
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
agent_topic = "buyer"
response_topic = "buyer_response"
negotiations = {}

def on_create(data: dict) -> dict | None:
    """
    Initialize the buyer agent with configuration.
    
    Args:
        data (dict): Configuration data including:
            - mqtt_broker: MQTT broker address
            - mqtt_port: MQTT broker port
            - negotiation_config: Buyer negotiation parameters
            
    Returns:
        dict | None: Initialization status
    """
    global config, mqtt_client
    
    config = data.copy()
    config.setdefault("mqtt_broker", "localhost")
    config.setdefault("mqtt_port", 1883)
    config.setdefault("name", "buyer")
    config.setdefault("negotiation_config", {
        "max_price": 250,
        "min_quantity": 1,
        "max_quantity": 10,
        "reserved_value": 0.1,
        "strategy": "BoulwareTBNegotiator"
    })
    
    # Initialize MQTT client
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    
    try:
        mqtt_client.connect(config["mqtt_broker"], config["mqtt_port"], 60)
        mqtt_client.loop_start()
        return {"status": "buyer_agent_initialized", "config": list(config.keys())}
    except Exception as e:
        return {"error": f"Failed to initialize MQTT: {str(e)}"}


def on_mqtt_connect(client, userdata, flags, rc):
    """Callback for when MQTT client connects"""
    if rc == 0:
        print(f"Buyer agent connected to MQTT broker")
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
    Main processing entry point for buyer agent negotiations.
    
    Args:
        data (list): List of negotiation messages/offers
                    
    Returns:
        dict: Buyer's response with decisions and counter-offers, including target topic
    """
    if not data:
        return {"result": json.dumps({"error": "No data provided"}), "agent": "buyer", "topic": "mechanism"}
    
    message = data[0]
    message_type = message.get("type", "unknown")
    session_id = message.get("session_id", "default")
    from_agent = message.get("from_agent", "mechanism")
    
    response = {
        "agent": "buyer",
        "session_id": session_id,
        "timestamp": time.time()
    }
    
    # Determine response topic based on message type and sender
    if message_type == "start_negotiation":
        response.update(handle_negotiation_start(message))
        target_topic = "mechanism"  # Respond to mechanism for coordination
    elif message_type == "offer":
        response.update(handle_offer(message))
        if from_agent == "seller":
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
    
    return {"result": json.dumps(response), "agent": "buyer", "topic": target_topic}


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
    
    # Generate initial offer based on buyer preferences
    initial_offer = generate_buyer_offer(outcome_space, session_id, is_initial=True)
    
    return {
        "type": "offer",
        "offer": initial_offer,
        "message": "Starting negotiation with initial buyer offer"
    }


def handle_offer(message: dict) -> dict:
    """Handle incoming offer from seller"""
    session_id = message.get("session_id")
    offer = message.get("offer", {})
    
    if session_id not in negotiations:
        return {"error": "Unknown negotiation session"}
    
    # Record received offer
    negotiations[session_id]["offers_received"].append(offer)
    
    # Evaluate offer
    utility = calculate_buyer_utility(offer)
    reserved_value = config["negotiation_config"]["reserved_value"]
    
    if utility >= reserved_value:
        # Accept offer
        negotiations[session_id]["status"] = "accepted"
        return {
            "type": "accept",
            "offer": offer,
            "utility": utility,
            "message": "Buyer accepts the offer"
        }
    else:
        # Generate counter-offer
        counter_offer = generate_buyer_offer(
            negotiations[session_id]["outcome_space"], 
            session_id, 
            previous_offer=offer
        )
        
        if counter_offer:
            return {
                "type": "offer",
                "offer": counter_offer,
                "message": "Buyer counter-offer"
            }
        else:
            # Reject if no viable counter-offer
            negotiations[session_id]["status"] = "rejected"
            return {
                "type": "reject",
                "message": "No acceptable offer can be made"
            }


def handle_acceptance(message: dict) -> dict:
    """Handle offer acceptance from seller"""
    session_id = message.get("session_id")
    
    if session_id in negotiations:
        negotiations[session_id]["status"] = "completed"
    
    return {
        "type": "negotiation_complete",
        "status": "success",
        "message": "Seller accepted buyer's offer"
    }


def handle_rejection(message: dict) -> dict:
    """Handle offer rejection from seller"""
    session_id = message.get("session_id")
    
    if session_id in negotiations:
        negotiations[session_id]["status"] = "rejected"
    
    return {
        "type": "negotiation_complete",
        "status": "failed",
        "message": "Seller rejected buyer's offer"
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


def generate_buyer_offer(outcome_space: dict, session_id: str, is_initial: bool = False, previous_offer: dict = None) -> dict:
    """Generate a buyer offer based on preferences and strategy"""
    
    # Extract price and quantity ranges from outcome space
    price_values = outcome_space.get("issues", {}).get("price", {}).get("values", [100, 150, 200, 250, 300])
    quantity_range = outcome_space.get("issues", {}).get("quantity", {}).get("values", [1, 10])
    
    buyer_config = config["negotiation_config"]
    max_price = buyer_config["max_price"]
    max_quantity = buyer_config["max_quantity"]
    
    if is_initial:
        # Start with buyer-favorable offer (low price, high quantity)
        price = min([p for p in price_values if p <= max_price])
        quantity = min(max_quantity, quantity_range[1])
    else:
        # Counter-offer: slight concession from previous position
        if previous_offer:
            prev_price = previous_offer.get("price", price_values[0])
            prev_quantity = previous_offer.get("quantity", quantity_range[0])
            
            # Make modest concession
            available_prices = [p for p in price_values if p <= max_price and p >= prev_price]
            if available_prices:
                price = available_prices[0] if len(available_prices) == 1 else available_prices[1]
            else:
                return None  # No viable offer
                
            quantity = min(max_quantity, prev_quantity)
        else:
            price = min([p for p in price_values if p <= max_price])
            quantity = min(max_quantity, quantity_range[1])
    
    offer = {"price": price, "quantity": quantity}
    
    # Record the offer
    if session_id in negotiations:
        negotiations[session_id]["offers_made"].append(offer)
    
    return offer


def calculate_buyer_utility(offer: dict) -> float:
    """Calculate utility of an offer from buyer's perspective"""
    price = offer.get("price", 0)
    quantity = offer.get("quantity", 0)
    
    # Buyer utility: lower price and higher quantity are better
    # Normalize to 0-1 scale
    max_price = config["negotiation_config"]["max_price"]
    max_quantity = config["negotiation_config"]["max_quantity"]
    
    price_utility = max(0, (max_price - price) / max_price)
    quantity_utility = quantity / max_quantity
    
    # Weighted combination (price weight higher for buyer)
    total_utility = 0.7 * price_utility + 0.3 * quantity_utility
    
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
        "agent": "buyer",
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
            "max_price": 250,
            "min_quantity": 1,
            "max_quantity": 10,
            "reserved_value": 0.3
        }
    }
    
    on_create(test_config)
    
    # Test negotiation start
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
    
    result = on_receive([start_message])
    print("Buyer Agent Test Result:")
    print(result["result"])
    
    on_destroy()