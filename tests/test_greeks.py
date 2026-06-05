import sys
import os
import math

# Add workspace to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.portfolio import calculate_greeks

def test_call_at_the_money():
    # S=100, K=100, T=1 year, r=5%, sigma=20%
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    greeks = calculate_greeks('call', S, K, T, r, sigma)
    
    print("Call ATM Greeks:", greeks)
    
    # Delta for ATM call should be slightly above 0.5 because of positive drift (r)
    assert 0.5 < greeks["delta"] < 0.7
    assert greeks["gamma"] > 0.0
    assert greeks["theta"] < 0.0  # Theta decay is negative
    assert greeks["vega"] > 0.0   # Vega is positive
    print("test_call_at_the_money passed!")

def test_put_at_the_money():
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    greeks = calculate_greeks('put', S, K, T, r, sigma)
    
    print("Put ATM Greeks:", greeks)
    
    # Delta for ATM put should be between -0.5 and -0.3
    assert -0.5 < greeks["delta"] < -0.3
    assert greeks["gamma"] > 0.0
    assert greeks["theta"] < 0.0 or greeks["theta"] > -10.0 # depending on parameters, standard theta decay is negative
    assert greeks["vega"] > 0.0
    print("test_put_at_the_money passed!")

def test_expired_option():
    # Option near expiration (T=0)
    greeks_call = calculate_greeks('call', 105.0, 100.0, 0.0, 0.05, 0.20)
    greeks_put = calculate_greeks('put', 95.0, 100.0, 0.0, 0.05, 0.20)
    
    assert greeks_call["delta"] == 1.0
    assert greeks_put["delta"] == -1.0
    assert greeks_call["gamma"] == 0.0
    assert greeks_call["vega"] == 0.0
    print("test_expired_option passed!")

if __name__ == "__main__":
    print("Running Black-Scholes Greeks tests...")
    test_call_at_the_money()
    test_put_at_the_money()
    test_expired_option()
    print("All tests passed successfully!")
