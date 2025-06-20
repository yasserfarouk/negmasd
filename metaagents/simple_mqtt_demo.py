#!/usr/bin/env python3
"""
Simple MQTT Demo for Negotiation Agents
Simulates MQTT communication without requiring a real broker
"""

import json
import time
import threading
from queue import Queue
from typing import Dict, List

class MockMQTTBroker:
    """Mock MQTT broker for testing"""
    
    def __init__(self):
        self.subscribers = {}  # topic -> [callback functions]
        self.message_queue = Queue()
        self.running = False
        
    def subscribe(self, topic, callback):
        """Subscribe to a topic"""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)
        print(f"📡 Subscribed to topic: {topic}")
    
    def publish(self, topic, message):
        """Publish message to topic"""
        print(f"📨 Publishing to {topic}: {message[:100]}...")
        if topic in self.subscribers:
            for callback in self.subscribers[topic]:
                try:
                    callback(topic, message)
                except Exception as e:
                    print(f"❌ Error in callback: {e}")
    
    def start(self):
        """Start the broker"""
        self.running = True
        print("🚀 Mock MQTT Broker started")
    
    def stop(self):
        """Stop the broker"""
        self.running = False
        print("🛑 Mock MQTT Broker stopped")


class MockAgent:
    """Base mock agent for testing"""
    
    def __init__(self, name, broker):
        self.name = name
        self.broker = broker
        self.config = {}
        
        # Subscribe to own topic
        self.broker.subscribe(name, self.on_message)
        
    def on_message(self, topic, message):
        """Handle incoming MQTT message"""
        try:
            data = json.loads(message)
            print(f"🔄 {self.name} received: {data.get('type', 'unknown')} message")
            
            # Process through on_receive
            response = self.on_receive([data])
            
            # Send response to specified topic
            if "topic" in response:
                self.broker.publish(response["topic"], response["result"])
        except Exception as e:
            print(f"❌ {self.name} message handling error: {e}")
    
    def on_create(self, data):
        """Initialize agent"""
        self.config = data
        return {"status": f"{self.name}_initialized"}
    
    def on_receive(self, data):
        """Process data - to be implemented by subclasses"""
        return {"result": json.dumps({"agent": self.name, "received": data}), "topic": "mechanism"}
    
    def on_destroy(self):
        """Cleanup"""
        return {"agent": self.name, "final_status": "destroyed"}


class MockBuyer(MockAgent):
    """Mock buyer agent"""
    
    def __init__(self, broker):
        super().__init__("buyer", broker)
        self.negotiations = {}
    
    def on_receive(self, data):
        if not data:
            return {"result": json.dumps({"error": "No data"}), "topic": "mechanism"}
        
        message = data[0]
        message_type = message.get("type")
        session_id = message.get("session_id", "default")
        
        response = {
            "agent": "buyer",
            "session_id": session_id,
            "timestamp": time.time()
        }
        
        if message_type == "start_negotiation":
            # Make initial offer
            offer = {"price": 150, "quantity": 8}
            response.update({
                "type": "offer",
                "offer": offer,
                "message": "Buyer initial offer"
            })
            
        elif message_type == "offer":
            # Evaluate seller's offer
            offer = message.get("offer", {})
            price = offer.get("price", 0)
            
            if price <= 200:  # Buyer accepts reasonable prices
                response.update({
                    "type": "accept",
                    "offer": offer,
                    "message": "Buyer accepts offer"
                })
            else:
                # Counter-offer
                counter = {"price": max(150, price - 30), "quantity": offer.get("quantity", 5)}
                response.update({
                    "type": "offer",
                    "offer": counter,
                    "message": "Buyer counter-offer"
                })
        
        return {"result": json.dumps(response), "topic": "mechanism"}


class MockSeller(MockAgent):
    """Mock seller agent"""
    
    def __init__(self, broker):
        super().__init__("seller", broker)
        self.negotiations = {}
    
    def on_receive(self, data):
        if not data:
            return {"result": json.dumps({"error": "No data"}), "topic": "mechanism"}
        
        message = data[0]
        message_type = message.get("type")
        session_id = message.get("session_id", "default")
        
        response = {
            "agent": "seller",
            "session_id": session_id,
            "timestamp": time.time()
        }
        
        if message_type == "start_negotiation":
            response.update({
                "type": "ready",
                "message": "Seller ready to negotiate"
            })
            
        elif message_type == "offer":
            # Evaluate buyer's offer
            offer = message.get("offer", {})
            price = offer.get("price", 0)
            
            if price >= 180:  # Seller accepts good prices
                response.update({
                    "type": "accept",
                    "offer": offer,
                    "message": "Seller accepts offer"
                })
            else:
                # Counter-offer
                counter = {"price": min(250, price + 40), "quantity": offer.get("quantity", 5)}
                response.update({
                    "type": "offer",
                    "offer": counter,
                    "message": "Seller counter-offer"
                })
        
        return {"result": json.dumps(response), "topic": "mechanism"}


class MockMechanism(MockAgent):
    """Mock mechanism agent"""
    
    def __init__(self, broker):
        super().__init__("mechanism", broker)
        self.negotiations = {}
        self.round_counter = {}
    
    def on_receive(self, data):
        if not data:
            return {"result": json.dumps({"error": "No data"}), "topic": "mechanism_response"}
        
        message = data[0]
        message_type = message.get("type")
        session_id = message.get("session_id", "default")
        agent = message.get("agent", "unknown")
        
        print(f"🎯 Mechanism processing {message_type} from {agent}")
        
        if message_type == "offer":
            return self.handle_offer(message, agent, session_id)
        elif message_type == "accept":
            return self.handle_accept(message, agent, session_id)
        elif message_type == "ready":
            return self.handle_ready(message, agent, session_id)
        
        return {"result": json.dumps({"received": message}), "topic": "mechanism_response"}
    
    def handle_offer(self, message, agent, session_id):
        """Handle offer from buyer or seller"""
        offer = message.get("offer", {})
        other_agent = "seller" if agent == "buyer" else "buyer"
        
        # Forward offer to other agent
        forward_message = {
            "type": "offer",
            "session_id": session_id,
            "offer": offer,
            "from_agent": agent,
            "round": self.round_counter.get(session_id, 0)
        }
        
        print(f"⚡ Forwarding offer from {agent} to {other_agent}: {offer}")
        self.broker.publish(other_agent, json.dumps(forward_message))
        
        # Increment round counter
        self.round_counter[session_id] = self.round_counter.get(session_id, 0) + 1
        
        return {
            "result": json.dumps({
                "type": "offer_forwarded",
                "from": agent,
                "to": other_agent,
                "offer": offer,
                "round": self.round_counter[session_id]
            }),
            "topic": "mechanism_response"
        }
    
    def handle_accept(self, message, agent, session_id):
        """Handle acceptance"""
        offer = message.get("offer", {})
        
        print(f"🎉 AGREEMENT REACHED! {agent} accepted: {offer}")
        
        # Notify both agents
        notification = {
            "type": "negotiation_complete",
            "session_id": session_id,
            "final_agreement": offer,
            "accepting_agent": agent
        }
        
        self.broker.publish("buyer", json.dumps(notification))
        self.broker.publish("seller", json.dumps(notification))
        
        return {
            "result": json.dumps({
                "type": "negotiation_completed",
                "final_agreement": offer,
                "accepting_agent": agent,
                "rounds": self.round_counter.get(session_id, 0)
            }),
            "topic": "mechanism_response"
        }
    
    def handle_ready(self, message, agent, session_id):
        """Handle ready message"""
        return {
            "result": json.dumps({
                "type": "agent_ready",
                "agent": agent,
                "session_id": session_id
            }),
            "topic": "mechanism_response"
        }
    
    def start_negotiation(self, session_id):
        """Start a negotiation session"""
        start_message = {
            "type": "start_negotiation",
            "session_id": session_id,
            "outcome_space": {
                "issues": {
                    "price": {"values": [100, 150, 200, 250, 300]},
                    "quantity": {"values": [1, 10]}
                }
            }
        }
        
        print(f"🚀 Starting negotiation {session_id}")
        self.broker.publish("buyer", json.dumps(start_message))
        self.broker.publish("seller", json.dumps(start_message))


def run_mqtt_demo():
    """Run the MQTT demo"""
    print("🎭 MQTT Negotiation Demo")
    print("=" * 50)
    
    # Create mock broker
    broker = MockMQTTBroker()
    broker.start()
    
    # Create agents
    buyer = MockBuyer(broker)
    seller = MockSeller(broker)
    mechanism = MockMechanism(broker)
    
    # Initialize agents
    buyer.on_create({"max_price": 220, "reserved_value": 0.4})
    seller.on_create({"min_price": 180, "reserved_value": 0.3})
    mechanism.on_create({"max_rounds": 5})
    
    print("\\n🏁 All agents initialized and connected")
    
    # Start negotiation
    print("\\n🔄 Starting negotiation...")
    mechanism.start_negotiation("demo_session_1")
    
    # Let the negotiation run for a bit
    time.sleep(2)
    
    print("\\n🏁 Demo completed!")
    broker.stop()
    
    return True


if __name__ == "__main__":
    try:
        success = run_mqtt_demo()
        if success:
            print("\\n✅ MQTT Demo completed successfully!")
            print("\\nThis demonstrates:")
            print("  - MQTT-style pub/sub communication")
            print("  - Agent message routing via topics") 
            print("  - Mechanism coordinating buyer-seller negotiation")
            print("  - on_create/on_receive/on_destroy pattern")
            print("\\n🚀 Ready for real MQTT broker deployment!")
    except Exception as e:
        print(f"\\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()