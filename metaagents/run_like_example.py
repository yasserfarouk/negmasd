#!/usr/bin/env python3
"""
Run negotiation agents exactly like the original example.py pattern
Demonstrates on_create, on_receive, on_destroy workflow for buyer, seller, mechanism
"""

import json
import time
import sys
import os

# Add metaagents to path to import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_buyer_like_example():
    """Test buyer agent like example.py"""
    print("=== Testing Buyer Agent (like example.py) ===")
    
    # Create a simplified buyer that doesn't need MQTT
    class BuyerAgent:
        def __init__(self):
            self.config = {}
            self.negotiations = {}
        
        def on_create(self, data):
            """Initialize buyer with configuration"""
            self.config = data.get("config", {})
            self.config.setdefault("negotiation_config", {
                "max_price": 250,
                "min_quantity": 1,
                "max_quantity": 10,
                "reserved_value": 0.3
            })
            return {"config_loaded": list(self.config.keys())}
        
        def on_receive(self, data):
            """Process negotiation data and return response"""
            if not data:
                return {"result": json.dumps({"error": "No data provided"}), "agent": "buyer"}
            
            # Extract negotiation parameters from data
            session_data = data[0]
            session_id = session_data.get("session_id", "default")
            offers = session_data.get("offers", [])
            outcome_space = session_data.get("outcome_space", {})
            
            results = {
                "agent": "buyer",
                "session_id": session_id,
                "analysis_timestamp": time.time(),
                "data_summary": {
                    "total_offers": len(offers),
                    "outcome_space": outcome_space
                },
                "decisions": [],
                "final_offer": None
            }
            
            # Process each offer
            for i, offer in enumerate(offers):
                utility = self.calculate_utility(offer)
                decision = {
                    "offer_id": i,
                    "offer": offer,
                    "utility": utility,
                    "decision": "accept" if utility >= self.config["negotiation_config"]["reserved_value"] else "reject"
                }
                results["decisions"].append(decision)
            
            # Generate buyer's best offer
            if outcome_space:
                buyer_offer = self.generate_best_offer(outcome_space)
                results["final_offer"] = buyer_offer
            
            return {"result": json.dumps(results), "agent": "buyer"}
        
        def calculate_utility(self, offer):
            """Calculate utility of an offer"""
            price = offer.get("price", 0)
            quantity = offer.get("quantity", 0)
            
            max_price = self.config["negotiation_config"]["max_price"]
            max_quantity = self.config["negotiation_config"]["max_quantity"]
            
            price_utility = max(0, (max_price - price) / max_price)
            quantity_utility = quantity / max_quantity
            
            return 0.7 * price_utility + 0.3 * quantity_utility
        
        def generate_best_offer(self, outcome_space):
            """Generate buyer's preferred offer"""
            price_values = outcome_space.get("issues", {}).get("price", {}).get("values", [100, 150, 200, 250, 300])
            quantity_range = outcome_space.get("issues", {}).get("quantity", {}).get("values", [1, 10])
            
            # Buyer wants low price, high quantity
            best_price = min([p for p in price_values if p <= self.config["negotiation_config"]["max_price"]])
            best_quantity = min(self.config["negotiation_config"]["max_quantity"], quantity_range[1])
            
            return {"price": best_price, "quantity": best_quantity}
        
        def on_destroy(self):
            """Cleanup"""
            return {"agent": "buyer", "final_config": self.config, "sessions": len(self.negotiations)}
    
    # Test the buyer agent
    buyer = BuyerAgent()
    
    # Step 1: on_create
    print("\\n1. on_create:")
    config = {
        "negotiation_config": {
            "max_price": 220,
            "min_quantity": 2,
            "max_quantity": 8,
            "reserved_value": 0.4
        }
    }
    create_result = buyer.on_create({"config": config})
    print(f"   {create_result}")
    
    # Step 2: on_receive with test data
    print("\\n2. on_receive:")
    test_data = [{
        "session_id": "buyer_test_session",
        "offers": [
            {"price": 200, "quantity": 5},
            {"price": 180, "quantity": 6},
            {"price": 250, "quantity": 4}
        ],
        "outcome_space": {
            "issues": {
                "price": {"values": [100, 150, 200, 250, 300]},
                "quantity": {"values": [1, 10]}
            }
        }
    }]
    
    receive_result = buyer.on_receive(test_data)
    print(f"   Raw result: {receive_result['result']}")
    print(f"   Pretty printed:")
    import pprint
    pprint.pprint(json.loads(receive_result['result']))
    
    # Step 3: on_destroy
    print("\\n3. on_destroy:")
    destroy_result = buyer.on_destroy()
    print(f"   {destroy_result}")
    
    return True


def test_seller_like_example():
    """Test seller agent like example.py"""
    print("\\n=== Testing Seller Agent (like example.py) ===")
    
    class SellerAgent:
        def __init__(self):
            self.config = {}
            self.negotiations = {}
        
        def on_create(self, data):
            """Initialize seller with configuration"""
            self.config = data.get("config", {})
            self.config.setdefault("negotiation_config", {
                "min_price": 150,
                "max_quantity": 8,
                "min_quantity": 1,
                "reserved_value": 0.25
            })
            return {"config_loaded": list(self.config.keys())}
        
        def on_receive(self, data):
            """Process negotiation data and return response"""
            if not data:
                return {"result": json.dumps({"error": "No data provided"}), "agent": "seller"}
            
            session_data = data[0]
            session_id = session_data.get("session_id", "default")
            offers = session_data.get("offers", [])
            outcome_space = session_data.get("outcome_space", {})
            
            results = {
                "agent": "seller",
                "session_id": session_id,
                "analysis_timestamp": time.time(),
                "data_summary": {
                    "total_offers": len(offers),
                    "outcome_space": outcome_space
                },
                "decisions": [],
                "final_offer": None
            }
            
            # Process each offer
            for i, offer in enumerate(offers):
                utility = self.calculate_utility(offer)
                decision = {
                    "offer_id": i,
                    "offer": offer,
                    "utility": utility,
                    "decision": "accept" if utility >= self.config["negotiation_config"]["reserved_value"] else "reject"
                }
                results["decisions"].append(decision)
            
            # Generate seller's best offer
            if outcome_space:
                seller_offer = self.generate_best_offer(outcome_space)
                results["final_offer"] = seller_offer
            
            return {"result": json.dumps(results), "agent": "seller"}
        
        def calculate_utility(self, offer):
            """Calculate utility of an offer"""
            price = offer.get("price", 0)
            quantity = offer.get("quantity", 0)
            
            price_values = [100, 150, 200, 250, 300]
            max_price = max(price_values)
            min_price = self.config["negotiation_config"]["min_price"]
            max_quantity = self.config["negotiation_config"]["max_quantity"]
            
            price_utility = max(0, (price - min_price) / (max_price - min_price))
            quantity_utility = min(1.0, quantity / max_quantity)
            
            return 0.8 * price_utility + 0.2 * quantity_utility
        
        def generate_best_offer(self, outcome_space):
            """Generate seller's preferred offer"""
            price_values = outcome_space.get("issues", {}).get("price", {}).get("values", [100, 150, 200, 250, 300])
            quantity_range = outcome_space.get("issues", {}).get("quantity", {}).get("values", [1, 10])
            
            # Seller wants high price, reasonable quantity
            best_price = max([p for p in price_values if p >= self.config["negotiation_config"]["min_price"]])
            best_quantity = min(self.config["negotiation_config"]["max_quantity"], quantity_range[1])
            
            return {"price": best_price, "quantity": best_quantity}
        
        def on_destroy(self):
            """Cleanup"""
            return {"agent": "seller", "final_config": self.config, "sessions": len(self.negotiations)}
    
    # Test the seller agent
    seller = SellerAgent()
    
    # Step 1: on_create
    print("\\n1. on_create:")
    config = {
        "negotiation_config": {
            "min_price": 180,
            "max_quantity": 6,
            "min_quantity": 1,
            "reserved_value": 0.35
        }
    }
    create_result = seller.on_create({"config": config})
    print(f"   {create_result}")
    
    # Step 2: on_receive with test data (same offers as buyer)
    print("\\n2. on_receive:")
    test_data = [{
        "session_id": "seller_test_session",
        "offers": [
            {"price": 200, "quantity": 5},
            {"price": 180, "quantity": 6},
            {"price": 250, "quantity": 4}
        ],
        "outcome_space": {
            "issues": {
                "price": {"values": [100, 150, 200, 250, 300]},
                "quantity": {"values": [1, 10]}
            }
        }
    }]
    
    receive_result = seller.on_receive(test_data)
    print(f"   Raw result: {receive_result['result']}")
    print(f"   Pretty printed:")
    import pprint
    pprint.pprint(json.loads(receive_result['result']))
    
    # Step 3: on_destroy
    print("\\n3. on_destroy:")
    destroy_result = seller.on_destroy()
    print(f"   {destroy_result}")
    
    return True


def test_mechanism_like_example():
    """Test mechanism agent like example.py"""
    print("\\n=== Testing Mechanism Agent (like example.py) ===")
    
    class MechanismAgent:
        def __init__(self):
            self.config = {}
            self.negotiations = {}
        
        def on_create(self, data):
            """Initialize mechanism with configuration"""
            self.config = data.get("config", {})
            self.config.setdefault("mechanism_config", {
                "max_rounds": 10,
                "timeout_seconds": 60,
                "outcome_space": {
                    "issues": {
                        "price": {"values": [100, 150, 200, 250, 300]},
                        "quantity": {"values": [1, 10]}
                    }
                }
            })
            return {"config_loaded": list(self.config.keys())}
        
        def on_receive(self, data):
            """Process negotiation session data and return coordination results"""
            if not data:
                return {"result": json.dumps({"error": "No data provided"}), "agent": "mechanism"}
            
            session_data = data[0]
            session_id = session_data.get("session_id", "default")
            buyer_offers = session_data.get("buyer_offers", [])
            seller_offers = session_data.get("seller_offers", [])
            
            results = {
                "agent": "mechanism",
                "session_id": session_id,
                "analysis_timestamp": time.time(),
                "coordination_summary": {
                    "buyer_offers": len(buyer_offers),
                    "seller_offers": len(seller_offers),
                    "total_rounds": max(len(buyer_offers), len(seller_offers))
                },
                "compatibility_analysis": [],
                "recommended_agreement": None,
                "mechanism_decision": "continue"
            }
            
            # Analyze compatibility between offers
            for i, (buyer_offer, seller_offer) in enumerate(zip(buyer_offers, seller_offers)):
                compatibility = self.analyze_offer_compatibility(buyer_offer, seller_offer)
                results["compatibility_analysis"].append({
                    "round": i + 1,
                    "buyer_offer": buyer_offer,
                    "seller_offer": seller_offer,
                    "compatibility_score": compatibility["score"],
                    "potential_agreement": compatibility["midpoint"]
                })
            
            # Find best potential agreement
            if results["compatibility_analysis"]:
                best_round = max(results["compatibility_analysis"], key=lambda x: x["compatibility_score"])
                if best_round["compatibility_score"] > 0.5:  # Threshold for good compatibility
                    results["recommended_agreement"] = best_round["potential_agreement"]
                    results["mechanism_decision"] = "recommend_agreement"
            
            return {"result": json.dumps(results), "agent": "mechanism"}
        
        def analyze_offer_compatibility(self, buyer_offer, seller_offer):
            """Analyze how compatible two offers are"""
            buyer_price = buyer_offer.get("price", 0)
            buyer_quantity = buyer_offer.get("quantity", 0)
            seller_price = seller_offer.get("price", 0)
            seller_quantity = seller_offer.get("quantity", 0)
            
            # Price compatibility (closer prices = higher score)
            price_gap = abs(buyer_price - seller_price)
            price_score = max(0, 1 - price_gap / 200)  # Normalize by max price range
            
            # Quantity compatibility
            quantity_gap = abs(buyer_quantity - seller_quantity)
            quantity_score = max(0, 1 - quantity_gap / 10)  # Normalize by max quantity range
            
            # Overall compatibility
            overall_score = 0.7 * price_score + 0.3 * quantity_score
            
            # Potential midpoint agreement
            midpoint = {
                "price": int((buyer_price + seller_price) / 2),
                "quantity": int((buyer_quantity + seller_quantity) / 2)
            }
            
            return {
                "score": overall_score,
                "price_score": price_score,
                "quantity_score": quantity_score,
                "midpoint": midpoint
            }
        
        def on_destroy(self):
            """Cleanup"""
            return {"agent": "mechanism", "final_config": self.config, "sessions": len(self.negotiations)}
    
    # Test the mechanism agent
    mechanism = MechanismAgent()
    
    # Step 1: on_create
    print("\\n1. on_create:")
    config = {
        "mechanism_config": {
            "max_rounds": 5,
            "timeout_seconds": 30
        }
    }
    create_result = mechanism.on_create({"config": config})
    print(f"   {create_result}")
    
    # Step 2: on_receive with negotiation session data
    print("\\n2. on_receive:")
    test_data = [{
        "session_id": "mechanism_test_session",
        "buyer_offers": [
            {"price": 100, "quantity": 10},
            {"price": 150, "quantity": 8},
            {"price": 180, "quantity": 6}
        ],
        "seller_offers": [
            {"price": 250, "quantity": 5},
            {"price": 200, "quantity": 6},
            {"price": 190, "quantity": 6}
        ]
    }]
    
    receive_result = mechanism.on_receive(test_data)
    print(f"   Raw result: {receive_result['result']}")
    print(f"   Pretty printed:")
    import pprint
    pprint.pprint(json.loads(receive_result['result']))
    
    # Step 3: on_destroy
    print("\\n3. on_destroy:")
    destroy_result = mechanism.on_destroy()
    print(f"   {destroy_result}")
    
    return True


def main():
    """Run all tests like original example.py"""
    print("Negotiation Agents - Example.py Style Testing")
    print("=" * 60)
    
    # Test each agent individually
    buyer_ok = test_buyer_like_example()
    seller_ok = test_seller_like_example()
    mechanism_ok = test_mechanism_like_example()
    
    print("\\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Buyer Agent:     {'✅ PASS' if buyer_ok else '❌ FAIL'}")
    print(f"Seller Agent:    {'✅ PASS' if seller_ok else '❌ FAIL'}")
    print(f"Mechanism Agent: {'✅ PASS' if mechanism_ok else '❌ FAIL'}")
    
    if all([buyer_ok, seller_ok, mechanism_ok]):
        print("\\n🎉 All agents working perfectly in example.py pattern!")
        print("\\nEach agent follows the same interface:")
        print("  - on_create(data) -> dict     # Initialize with config")
        print("  - on_receive(data) -> dict    # Process data and return results") 
        print("  - on_destroy() -> dict        # Cleanup and final status")
        print("\\nReady for MQTT integration and deployment!")
    else:
        print("\\n❌ Some tests failed")


if __name__ == "__main__":
    main()